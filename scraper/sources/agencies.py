"""Generic adapter for the long tail of agency websites, driven by agencies.json.

Each entry is fetched (plain HTTP, headless browser, PDF, or ArcGIS feature layer), reduced to text plus
a list of links, and handed to Claude for extraction. Results are cached by content hash, so unchanged
pages cost nothing on later runs.
"""
import io
import json
import re
import traceback
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .. import llm
from ..common import SESSION, Listing, clean, parse_money, state_abbr

CONFIG = Path(__file__).resolve().parents[2] / "agencies.json"
STATUS = {}       # id -> {"ok", "count", "error", "cached"}
OK_SOURCES = set()

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


def _pdf_text(data):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in r.pages[:60])


def _html_to_text(html, base):
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
        t.decompose()
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        txt = clean(a.get_text(" "))
        if href.startswith(("mailto:", "javascript:", "tel:")) or href in seen:
            continue
        seen.add(href)
        if txt or href.lower().endswith(".pdf"):
            links.append((txt[:120], href))
    main = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    text = re.sub(r"\n\s*\n+", "\n\n", main.get_text("\n"))
    return text, links


def _fetch(src):
    """Return (text, links, features). features is a list of GeoJSON features for ArcGIS sources."""
    kind = src.get("kind", "page")
    url = src["url"]
    if kind == "arcgis":
        q = url.rstrip("/") + "/query"
        r = SESSION.get(q, params={"where": "1=1", "outFields": "*", "returnGeometry": "true",
                                   "outSR": "4326", "f": "geojson"}, timeout=90)
        r.raise_for_status()
        feats = r.json().get("features", [])
        rows = [{"feature_index": i, **(f.get("properties") or {})} for i, f in enumerate(feats)]
        return "GIS FEATURE TABLE (one row per parcel):\n" + json.dumps(rows, default=str)[:80000], [], feats
    if kind == "pdf" or url.lower().endswith(".pdf"):
        r = SESSION.get(url, timeout=90, headers={"User-Agent": BROWSER_UA})
        r.raise_for_status()
        return _pdf_text(r.content), [], []
    if src.get("render"):
        from ..browser import capture
        cap = capture(f"agency_{src['id']}", url, wait_ms=src.get("wait_ms", 4000))
        text, links = _html_to_text(cap["html"], url)
        return text, links, []
    r = SESSION.get(url, timeout=60, headers={"User-Agent": BROWSER_UA})
    r.raise_for_status()
    if "pdf" in r.headers.get("content-type", ""):
        return _pdf_text(r.content), [], []
    text, links = _html_to_text(r.text, r.url)
    if len(clean(text)) < 400:  # probably a JS app — retry rendered
        from ..browser import capture
        cap = capture(f"agency_{src['id']}", url, wait_ms=src.get("wait_ms", 4000))
        text, links = _html_to_text(cap["html"], url)
    return text, links, []


def _follow(src, links):
    """Optionally pull detail pages / PDFs whose link text or URL matches src['follow']."""
    pat = src.get("follow")
    if not pat:
        return ""
    rx = re.compile(pat, re.I)
    out = []
    for txt, href in links:
        if len(out) >= src.get("max_follow", 12):
            break
        if not (rx.search(href) or rx.search(txt)):
            continue
        try:
            r = SESSION.get(href, timeout=60, headers={"User-Agent": BROWSER_UA})
            r.raise_for_status()
            if href.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
                body = _pdf_text(r.content)
            else:
                body, _ = _html_to_text(r.text, href)
            out.append(f"\n\n=== DETAIL PAGE: {href} ===\n{clean(body)[:12000]}")
        except Exception as e:
            out.append(f"\n\n=== DETAIL PAGE: {href} === (failed: {e})")
    return "".join(out)


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_money("$" + str(v).lstrip("$")) if re.search(r"\d", str(v)) else None


def _one(src):
    text, links, feats = _fetch(src)
    detail = _follow(src, links)
    link_block = "\n".join(f"- {t} -> {h}" for t, h in links[:400])
    content = f"PAGE TEXT:\n{clean(text) if not feats else text}\n{detail}\n\nLINKS:\n{link_block}"
    result, cached = llm.extract(src["id"], content, {"agency": src["agency"], "state": src.get("state"), "url": src["url"]})
    out = []
    for i, it in enumerate(result.get("listings") or []):
        title = clean(it.get("title")) or f"{src['agency']} property"
        url = it.get("url") or src["url"]
        if url and not url.startswith("http"):
            url = urljoin(src["url"], url)
        sid = clean(str(it.get("parcel_id") or "")) or f"{title}|{it.get('address') or ''}"
        st = state_abbr(it.get("state") or "") or src.get("state")
        l = Listing(
            source=f"agency:{src['id']}", source_id=sid, title=title, url=url, agency=src["agency"],
            state=st, county=clean(it.get("county")) or None, city=clean(it.get("city")) or None,
            acres=_num(it.get("acres")), price=_num(it.get("price")), price_type=it.get("price_type"),
            sale_type=it.get("sale_type"), stage=it.get("stage"), sale_date=(it.get("sale_date") or None),
            summary=clean(it.get("summary"))[:600],
            extra={"address": clean(it.get("address")), "parcel_id": clean(str(it.get("parcel_id") or "")),
                   "property_type": it.get("property_type") or "", "source_name": src["name"],
                   "level": src["level"], "category": src.get("category", "surplus"),
                   "listing_page": src["url"]},
        )
        fi = it.get("feature_index")
        if feats and isinstance(fi, int) and 0 <= fi < len(feats) and feats[fi].get("geometry"):
            from .energynet import _centroid, _round_geom
            g = _round_geom(feats[fi]["geometry"])
            if g:
                l.geometry = g
                l.lat, l.lon = _centroid(g)
                l.loc_precision = "parcel"
        out.append(l)
    return out, cached, result.get("page_status", "")


def fetch():
    sources = [s for s in json.loads(CONFIG.read_text())["sources"] if s.get("enabled", True)]
    if not llm.available():
        print("agencies: ANTHROPIC_API_KEY not set — checking reachability only")
        for src in sources:
            try:
                text, links, feats = _fetch(src)
                STATUS[src["id"]] = {"ok": False, "error": "no API key", "chars": len(text), "links": len(links)}
                print(f"  {src['id']}: reachable, {len(text)} chars, {len(links)} links, {len(feats)} features")
            except Exception as e:
                STATUS[src["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
                print(f"  {src['id']}: ERROR {type(e).__name__}: {str(e)[:200]}")
        return []
    out = []
    for src in sources:
        try:
            items, cached, status = _one(src)
            out += items
            OK_SOURCES.add(f"agency:{src['id']}")
            STATUS[src["id"]] = {"ok": True, "count": len(items), "cached": cached, "note": status[:200]}
            print(f"  {src['id']}: {len(items)} listings{' (cached)' if cached else ''} {status[:80]}")
        except Exception as e:
            STATUS[src["id"]] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
            print(f"  {src['id']}: ERROR {type(e).__name__}: {str(e)[:200]}")
            traceback.print_exc(limit=1)
    return out

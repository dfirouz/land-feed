"""GSA surplus federal real property — realestatesales.gov listing cards + detail pages."""
import re

from ..browser import capture
from ..common import Listing, clean, parse_acres, parse_money, state_abbr

BASE = "https://realestatesales.gov"
LIST_URL = BASE + "/our-listing/"
STATUSES = ("NOW BIDDING", "COMING SOON", "ACTIVE", "CLOSED", "SOLD", "PENDING", "ENDED")
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"


def _iso(s):
    import datetime as dt
    try:
        return dt.datetime.strptime(clean(s), "%b %d, %Y").date().isoformat()
    except Exception:
        return None


def _parse_card(text):
    lines = [clean(x) for x in text.split("\n") if clean(x)]
    d = {"current_bid": None, "status": None, "name": None, "street": None, "city": None,
         "state": None, "zip": None, "when": None, "method": None, "category": None}
    i = 0
    if lines and lines[0].lower().startswith("current bid"):
        d["current_bid"] = parse_money(lines[1]) if len(lines) > 1 else None
        i = 2
    if i < len(lines) and lines[i].upper() in STATUSES:
        d["status"] = lines[i].title(); i += 1
    rest = lines[i:]
    if rest:
        d["name"] = rest[0]
    for j, l in enumerate(rest[1:], 1):
        m = re.match(r"^(.+?),\s*([A-Z]{2})\s*(\d{5})?", l)
        if m and state_abbr(m.group(2)) and not d["city"]:
            d["city"], d["state"], d["zip"] = m.group(1), m.group(2), m.group(3)
            d["street"] = rest[j - 1] if j - 1 > 0 else None
            tail = rest[j + 1:]
            if tail and re.match(r"(Auction|Bidding|Sale|Offers)", tail[0], re.I):
                d["when"] = tail.pop(0)
            if tail:
                d["method"] = tail[0]
            if len(tail) > 1:
                d["category"] = tail[1]
            break
    return d


def _detail(pid):
    """Pull acreage / starting bid from the asset detail page text."""
    try:
        cap = capture(f"gsa_{pid}", f"{BASE}/asset-details/?property_id={pid}", wait_ms=1500, scroll=False)
    except Exception as e:
        print(f"  gsa detail {pid}: {e}")
        return {}
    t = cap["text"]
    out = {}
    m = re.search(r"(?:Lot Size|Land Area|Acreage|Site Area|Acres)\s*:?\s*\n?\s*([\d,]+(?:\.\d+)?)\s*(acres?|ac|sq\.?\s*ft|square feet|sf)?", t, re.I)
    if m:
        v = float(m.group(1).replace(",", ""))
        unit = (m.group(2) or "acres").lower()
        out["acres"] = v / 43560 if ("sq" in unit or unit in ("sf", "square feet")) else v
    else:
        out["acres"] = parse_acres(t[:20000])
    for label in ("Starting Bid", "Opening Bid", "Minimum Bid", "Asking Price", "List Price", "Price"):
        m = re.search(label + r"\s*:?\s*\n?\s*(\$[\d,]+(?:\.\d+)?)", t, re.I)
        if m:
            out["start_price"], out["price_label"] = parse_money(m.group(1)), label
            break
    m = re.search(r"(?:Description|Property Description|Overview)\s*\n(.{40,600})", t)
    out["summary"] = clean(m.group(1)) if m else ""
    return out


def fetch():
    cap = capture("gsa", LIST_URL, wait_ms=3000)
    out, seen = [], set()
    for l in cap["links"]:
        m = re.search(r"asset-details/\?property_id=(\d+)", l["href"])
        if not m or m.group(1) in seen:
            continue
        pid = m.group(1)
        seen.add(pid)
        c = _parse_card(l["text"] or "")
        if not c["name"]:
            continue
        det = _detail(pid)
        when = c["when"] or ""
        dates = re.findall(rf"((?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}})", when)
        price, ptype = None, None
        if c["current_bid"]:
            price, ptype = c["current_bid"], "Current bid"
        elif det.get("start_price"):
            price, ptype = det["start_price"], det.get("price_label")
        sale_type = " · ".join(x for x in (c["method"], c["category"]) if x) or None
        out.append(Listing(
            source="gsa", source_id=pid, title=c["name"],
            url=f"{BASE}/asset-details/?property_id={pid}", agency="GSA",
            state=c["state"], city=c["city"],
            acres=det.get("acres") or parse_acres(c["name"]),
            price=price, price_type=ptype, sale_type=sale_type, stage=c["status"],
            sale_date=_iso(dates[-1]) if dates else None,
            summary=det.get("summary", ""),
            extra={"address": c["street"] or "", "zip": c["zip"] or "", "property_type": c["category"] or "",
                   "auction_window": when},
        ))
    return out

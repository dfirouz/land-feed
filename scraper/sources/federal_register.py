"""BLM land sales via Notices of Realty Action in the Federal Register (official API, no key)."""
import datetime as dt
import re

from ..common import SESSION, Listing, clean, parse_acres, parse_county_state, parse_money, state_abbr

API = "https://www.federalregister.gov/api/v1/documents.json"
LOOKBACK_DAYS = 400

MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = rf"((?:{MONTHS})\s+\d{{1,2}},\s+\d{{4}})"


def _to_iso(s):
    try:
        return dt.datetime.strptime(s.replace("  ", " "), "%B %d, %Y").date().isoformat()
    except Exception:
        return None


def _sale_type(title):
    t = title.lower()
    if "modified competitive" in t:
        return "Modified competitive"
    if "competitive" in t and "non" not in t.split("competitive")[0][-5:]:
        return "Competitive"
    if "direct" in t or "noncompetitive" in t or "non-competitive" in t:
        return "Direct (non-competitive)"
    return None


def _sale_date(text):
    # Look for the auction / bid opening date
    for pat in [
        rf"(?:auction|bid opening|sale)\s+(?:will\s+(?:be\s+held|take\s+place|begin|open|occur)|is\s+scheduled)[^.]{{0,120}}?{DATE_RE}",
        rf"(?:sealed bids|bids)\s+(?:must\s+be\s+received|are\s+due)[^.]{{0,80}}?{DATE_RE}",
        rf"(?:online|oral)\s+auction[^.]{{0,120}}?{DATE_RE}",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            return _to_iso(m.group(1))
    return None


def _comment_deadline(dates_text):
    m = re.search(rf"(?:comments?|objections?)[^.]{{0,100}}?(?:until|by|on or before|no later than)\s+{DATE_RE}", dates_text or "", re.I)
    return _to_iso(m.group(1)) if m else None


def _fmv(text):
    m = re.search(
        r"(?:fair market value|FMV|appraised value|minimum bid|minimum acceptable bid)[^.$]{0,120}?(\$\s*[\d,]+(?:\.\d+)?(?:\s*million)?)",
        text, re.I,
    )
    return parse_money(m.group(1)) if m else None


def fetch():
    since = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    params = [
        ("conditions[agencies][]", "land-management-bureau"),
        ("conditions[type][]", "NOTICE"),
        ("conditions[term]", '"realty action"'),
        ("conditions[publication_date][gte]", since),
        ("per_page", "100"),
        ("order", "newest"),
    ]
    for f in ["title", "abstract", "html_url", "publication_date", "document_number", "dates", "raw_text_url"]:
        params.append(("fields[]", f))

    docs, url, p = [], API, params
    while url:
        r = SESSION.get(url, params=p, timeout=60)
        r.raise_for_status()
        j = r.json()
        docs += j.get("results", [])
        url, p = j.get("next_page_url"), None

    out = []
    for d in docs:
        title = clean(d.get("title"))
        tl = title.lower()
        if "sale" not in tl:
            continue
        abstract = clean(d.get("abstract"))
        raw = ""
        if d.get("raw_text_url"):
            try:
                rr = SESSION.get(d["raw_text_url"], timeout=60)
                if rr.ok:
                    raw = clean(rr.text)[:60000]
            except Exception:
                pass
        text = f"{title}. {abstract} {raw}"
        county, st = parse_county_state(title)
        if not st:
            county, st = parse_county_state(abstract or raw[:4000])
        if not st:
            m = re.search(r",\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+)?|[A-Z]{2})\s*;?\s*(?:and|$|Termination)", title)
            st = state_abbr(m.group(1)) if m else None
        sale_date = _sale_date(clean(d.get("dates")) + " " + (raw or abstract))
        stage = "Notice of Realty Action"
        if sale_date and sale_date < dt.date.today().isoformat():
            stage = "Sale date passed"
        price = _fmv(raw)
        out.append(Listing(
            source="federal_register",
            source_id=d["document_number"],
            title=title,
            url=d["html_url"],
            agency="BLM",
            state=st,
            county=county,
            acres=parse_acres(abstract) or parse_acres(raw[:8000]),
            price=price,
            price_type="FMV / minimum bid" if price else None,
            sale_type=_sale_type(title),
            stage=stage,
            sale_date=sale_date,
            comment_deadline=_comment_deadline(d.get("dates")),
            published=d.get("publication_date"),
            summary=abstract[:600],
        ))
    return out

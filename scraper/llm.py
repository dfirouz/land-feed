"""Claude-powered extraction: turn any agency listing page / PDF / GIS table into listing records."""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests

MODEL = os.environ.get("EXTRACT_MODEL", "claude-haiku-5-5")
CACHE = Path("state/llm_cache")
API = "https://api.anthropic.com/v1/messages"
MAX_CHARS = 90_000

SYSTEM = """You extract public-sector real estate sale listings from government web pages, PDFs and GIS tables.
Return ONLY a JSON object: {"listings": [...], "page_status": "..."}.

Include an item only if a public agency is offering, or has formally noticed its intent to offer, a specific
property (land, lot, parcel, building, or real property interest) for sale or auction. Exclude: vehicles,
equipment, leases/rentals (unless sale), oil/gas/mineral leases, closed/awarded results older than 60 days,
general program descriptions, and generic links like "how to buy". If the page says there are no current
listings, return an empty list and say so in page_status.

Each listing object (use null when unknown; never invent values):
{
 "title": short name of the property,
 "url": the most specific link for this property (absolute URL; use one from the page's LINKS list when possible),
 "state": 2-letter US state code,
 "county": county name with "County"/"Parish" suffix,
 "city": city or nearest place,
 "address": street address or location description,
 "parcel_id": APN / parcel / tract / sale number,
 "acres": total acres as a number (convert square feet: sqft/43560),
 "price": number in USD — minimum bid, starting bid, asking price, or appraised value used as minimum,
 "price_type": what the price is, e.g. "Minimum bid", "Starting bid", "Asking price", "Current bid", "Appraised value",
 "sale_type": e.g. "Online auction", "Sealed bid", "Oral auction", "Negotiated sale", "Invitation to offer", "Tax deed sale",
 "stage": e.g. "Upcoming", "Open for bidding", "Accepting offers", "Notice of intent to sell", "Under contract", "Sold",
 "sale_date": ISO date (YYYY-MM-DD) when bids are due / auction closes / sale occurs,
 "property_type": e.g. "Vacant land", "Farmland", "Commercial", "Industrial", "Residential", "Timberland",
 "summary": one or two plain sentences on what it is (zoning, frontage, use) — no marketing fluff,
 "feature_index": for GIS tables only, the index of the source feature, else null
}"""


def _key():
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def available():
    return bool(_key())


def extract(source_id, content, context, force=False):
    """content: text to analyse. context: dict(agency, state, url). Cached by content hash."""
    content = content[:MAX_CHARS]
    h = hashlib.sha256((SYSTEM + json.dumps(context, sort_keys=True) + content).encode()).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "_", source_id)
    cpath = CACHE / f"{safe}.json"
    if cpath.exists() and not force:
        try:
            c = json.loads(cpath.read_text())
            if c.get("hash") == h:
                return c["result"], True
        except Exception:
            pass
    if not available():
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    user = (f"Agency: {context.get('agency')}\nDefault state: {context.get('state') or 'unknown'}\n"
            f"Page URL: {context.get('url')}\nToday: {time.strftime('%Y-%m-%d')}\n\n{content}")
    body = {"model": MODEL, "max_tokens": 8000, "system": SYSTEM,
            "messages": [{"role": "user", "content": user}]}
    for attempt in range(4):
        r = requests.post(API, json=body, timeout=180, headers={
            "x-api-key": _key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
        if r.status_code in (429, 500, 502, 503, 529):
            time.sleep(10 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    else:
        r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        result = json.loads(m.group(0)) if m else {"listings": [], "page_status": "unparseable"}
    except json.JSONDecodeError:
        result = {"listings": [], "page_status": "invalid JSON from model"}
    cpath.write_text(json.dumps({"hash": h, "at": time.strftime("%Y-%m-%d"), "result": result}, indent=1))
    return result, False

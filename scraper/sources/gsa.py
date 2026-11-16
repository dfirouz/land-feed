"""GSA surplus federal real property — realestatesales.gov (JS app; parsed from its JSON API calls)."""
import re

from ..browser import capture, walk_records
from ..common import Listing, clean, parse_acres, parse_money, state_abbr

BASE = "https://realestatesales.gov"


def _get(rec, *keys):
    """Case/format-insensitive key lookup; returns first non-empty match."""
    norm = {re.sub(r"[^a-z]", "", k.lower()): v for k, v in rec.items()}
    for k in keys:
        v = norm.get(re.sub(r"[^a-z]", "", k.lower()))
        if v not in (None, "", [], {}):
            return v
    return None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_money("$" + str(v).lstrip("$")) if re.search(r"\d", str(v)) else None


def _looks_like_property(rec):
    keys = {re.sub(r"[^a-z]", "", k.lower()) for k in rec}
    has_name = keys & {"name", "title", "propertyname", "auctionname", "listingname"}
    has_loc = keys & {"city", "state", "address", "street", "propertycity", "propertystate", "zip", "zipcode"}
    return bool(has_name and has_loc)


def fetch():
    cap = capture("gsa", BASE + "/", clicks=("text=View All Properties",))
    seen, out = set(), []
    for pl in cap["json"]:
        for rec in walk_records(pl["data"]):
            if not _looks_like_property(rec):
                continue
            name = clean(str(_get(rec, "name", "title", "propertyName", "auctionName", "listingName")))
            rid = str(_get(rec, "id", "propertyId", "auctionId", "listingId", "salesId", "lotId") or name)
            if rid in seen:
                continue
            seen.add(rid)
            city = _get(rec, "city", "propertyCity")
            st = state_abbr(str(_get(rec, "state", "propertyState", "stateCode") or ""))
            desc = clean(str(_get(rec, "description", "shortDescription", "summary", "propertyDescription") or ""))
            acres = _num(_get(rec, "acres", "acreage", "lotSizeAcres", "landAcres", "totalAcres")) or parse_acres(desc + " " + name)
            price = _num(_get(rec, "currentBid", "startingBid", "openingBid", "minimumBid", "price", "askingPrice", "listPrice"))
            ptype = "Current bid" if _get(rec, "currentBid") else ("Starting bid" if price else None)
            slug = _get(rec, "url", "link", "slug", "detailUrl", "href")
            url = slug if (isinstance(slug, str) and slug.startswith("http")) else (
                f"{BASE}/{str(slug).lstrip('/')}" if slug else f"{BASE}/")
            ptype_cat = clean(str(_get(rec, "propertyType", "category", "type", "assetType") or ""))
            out.append(Listing(
                source="gsa", source_id=rid, title=name, url=url, agency="GSA",
                state=st, city=clean(str(city)) if city else None,
                acres=acres, price=price, price_type=ptype,
                sale_type=clean(str(_get(rec, "auctionType", "saleType", "saleMethod") or "")) or None,
                stage=clean(str(_get(rec, "status", "auctionStatus", "saleStatus") or "")) or None,
                sale_date=str(_get(rec, "endDate", "auctionEndDate", "closeDate", "bidEndDate", "saleDate") or "")[:10] or None,
                summary=desc[:600],
                extra={"address": clean(str(_get(rec, "address", "street", "streetAddress") or "")),
                       "zip": str(_get(rec, "zip", "zipCode", "postalCode") or ""),
                       "property_type": ptype_cat},
            ))
    return out

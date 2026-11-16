"""EfficientMarkets (formerly EnergyNet) government & real-estate listings: BLM land sales
(e.g. SNPLMA) and state trust land auctions (e.g. Utah SITLA). Oil/gas/mineral leases skipped."""
import re

from ..browser import capture, walk_records
from ..common import Listing, clean, parse_county_state, parse_money, state_abbr

PAGES = {
    "energynet_gov": "https://app.efficientmarkets.com/government-listings",
    "energynet_re": "https://app.efficientmarkets.com/real-estate-listings",
}
SG_URL = "https://app.efficientmarkets.com/salegroup/{}"
SKIP_RE = re.compile(r"\blease\b|oil|gas|mineral|geothermal|coal|royalt|working interest", re.I)
LAND_RE = re.compile(r"land|real estate|surface|parcel|lot|acre", re.I)
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"


def _norm(rec):
    return {re.sub(r"[^a-z]", "", k.lower()): v for k, v in rec.items()}


def _first(n, *keys):
    for k in keys:
        v = n.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _agency(title, seller):
    s = f"{seller} {title}"
    if re.search(r"\bBLM\b|Bureau of Land Management", s):
        return "BLM"
    if "SITLA" in s or "Utah State Trust" in s:
        return "Utah SITLA"
    return clean(seller) or title.split("|")[0].strip()


def _iso(s):
    import datetime as dt
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            pass
    return None


def _groups_from_text(text):
    """Blocks look like:
    Sale Group # 7599
    <Category> | <Title>
    <n> tract(s)/parcel(s) covering 84.33 acres in Texas
    <Seller>
    Ends Nov 17, 2026, 9:00 AM CST
    """
    out = []
    parts = re.split(r"Sale Group #\s*(\d+)\s*\n", text)
    for i in range(1, len(parts) - 1, 2):
        sg, body = parts[i], parts[i + 1]
        lines = [clean(x) for x in body.split("\n") if clean(x)][:8]
        if not lines:
            continue
        head = lines[0]
        cat, _, title = head.partition("|")
        title = clean(title) or clean(cat)
        cover = next((l for l in lines if re.search(r"covering", l, re.I)), "")
        m = re.search(r"(\d[\d,]*)\s+(tracts?|parcels?|lots?)\s+(?:offered\s+)?covering\s+(?:approximately\s+)?([\d,.]+)\s+(?:net\s+mineral\s+|gross\s+)?acres(?:\s+in\s+(.+))?", cover, re.I)
        when = next((l for l in lines if re.match(r"(Ends|Starts|Opens|Closes|Begins|Ended|Bidding)", l, re.I)), "")
        dm = re.search(rf"((?:{MONTHS})[a-z]*\.?\s+\d{{1,2}},\s+\d{{4}})", when)
        seller_idx = lines.index(cover) + 1 if cover in lines else 1
        seller = lines[seller_idx] if seller_idx < len(lines) and lines[seller_idx] != when else ""
        out.append({
            "sg": sg, "category": clean(cat), "title": title, "seller": seller,
            "n": m.group(1) if m else None, "unit": m.group(2) if m else None,
            "acres": float(m.group(3).replace(",", "")) if m else None,
            "state": state_abbr(m.group(4)) if m and m.group(4) else None,
            "when": when, "date": _iso(dm.group(1).replace(".", "")) if dm else None,
        })
    return out


def fetch():
    groups = {}
    for name, url in PAGES.items():
        try:
            cap = capture(name, url, wait_ms=5000)
        except Exception as e:
            print(f"  {name}: {e}")
            continue
        for g in _groups_from_text(cap["text"]):
            full = f"{g['category']} {g['title']}"
            if SKIP_RE.search(full) or not LAND_RE.search(full + " " + (g["unit"] or "")):
                continue
            groups[g["sg"]] = g

    out = []
    for sg, g in groups.items():
        url = SG_URL.format(sg)
        title = g["title"]
        stage = ("Open for bidding" if re.match(r"Ends|Closes", g["when"], re.I)
                 else "Upcoming" if re.match(r"Starts|Opens|Begins", g["when"], re.I) else clean(g["when"]) or None)
        lots = []
        try:
            cap = capture(f"energynet_sg{sg}", url, wait_ms=5000)
            for pl in cap["json"]:
                for rec in walk_records(pl["data"]):
                    n = _norm(rec)
                    acres = _first(n, "acres", "acreage", "grossacres", "totalacres", "surfaceacres")
                    lot = _first(n, "lotnumber", "lotid", "lotno", "parcelnumber", "parcelid", "tractnumber", "lot")
                    if acres is None or lot is None or isinstance(lot, (dict, list)):
                        continue
                    try:
                        acres = float(str(acres).replace(",", ""))
                    except ValueError:
                        continue
                    price = _first(n, "minimumbid", "minbid", "minimumacceptablebid", "fmv", "fairmarketvalue",
                                   "appraisedvalue", "startingbid", "openingbid", "reserveprice")
                    cur = _first(n, "currentbid", "highbid", "highestbid")
                    price = float(price) if isinstance(price, (int, float)) else parse_money("$" + str(price)) if price else None
                    county = _first(n, "county", "countyname", "countystring", "counties")
                    if isinstance(county, list):
                        county = ", ".join(map(str, county))
                    st = state_abbr(str(_first(n, "state", "statecode", "statename") or "")) or g["state"]
                    legal = clean(str(_first(n, "legaldescription", "legal", "description", "shortdescription") or ""))
                    lid = str(_first(n, "id", "lotid") or lot)
                    lat, lon = _first(n, "latitude", "lat", "centroidlatitude"), _first(n, "longitude", "lon", "lng", "centroidlongitude")
                    l = Listing(
                        source="energynet", source_id=f"{sg}:{lid}",
                        title=f"{title} — Parcel {lot}", url=url, agency=_agency(title, g["seller"]),
                        state=st, county=(clean(str(county)) + ("" if "count" in str(county).lower() else " County")) if county else None,
                        acres=acres, price=price, price_type="Minimum bid" if price else None,
                        sale_type="Online auction", stage=stage, sale_date=g["date"],
                        summary=legal[:400],
                        extra={"sale_group": title, "current_bid": cur, "apn": str(_first(n, "apn", "assessorparcelnumber") or "")},
                    )
                    try:
                        if lat and lon:
                            l.lat, l.lon, l.loc_precision = float(lat), float(lon), "parcel"
                    except (TypeError, ValueError):
                        pass
                    lots.append(l)
            if not lots:
                county, st = parse_county_state(cap["text"][:6000])
                g["county"] = county
                g["state"] = g["state"] or st
        except Exception as e:
            print(f"  energynet sg {sg}: {e}")
        if lots:
            out += lots
            continue
        out.append(Listing(
            source="energynet", source_id=sg, title=title, url=url, agency=_agency(title, g["seller"]),
            state=g["state"], county=g.get("county"), acres=g["acres"],
            sale_type="Online auction", stage=stage, sale_date=g["date"],
            summary=f"{g['category']}. {g['n'] or ''} {g['unit'] or ''} covering {g['acres'] or '?'} acres. {g['when']}",
        ))
    return out

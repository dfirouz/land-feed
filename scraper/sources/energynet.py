"""EfficientMarkets (formerly EnergyNet) — the auction platform BLM and several state trust land
offices (Utah, Wyoming, New Mexico, ...) use. Uses the site's public JSON API.
Oil/gas/mineral/geothermal *lease* sales and private broker listings are skipped."""
import datetime as dt
import re

from ..common import SESSION, Listing, clean, parse_money, state_abbr

API = "https://api.efficientmarkets.com/v1"
DETAIL = "https://app.efficientmarkets.com/detail/{}"
GROUP = "https://app.efficientmarkets.com/salegroup/{}"
SKIP_RE = re.compile(r"\blease\b|oil|\bgas\b|mineral|geothermal|coal|royalt|working interest|potash|helium", re.I)
HDRS = {"Accept": "application/json", "Origin": "https://app.efficientmarkets.com",
        "Referer": "https://app.efficientmarkets.com/"}


def _get(path, **params):
    r = SESSION.get(f"{API}/{path}", params=params, headers=HDRS, timeout=60)
    r.raise_for_status()
    return r.json()


def _listings(sale_type):
    out, skip = [], 0
    while True:
        j = _get("Lot/Listings", saleType=sale_type, take=50, skip=skip)
        batch = j.get("listings", [])
        out += batch
        if len(batch) < 50 or skip > 1000:
            return out
        skip += 50


def _agency(seller):
    s = seller or ""
    if "Bureau of Land Management" in s or s.startswith("BLM"):
        return "BLM"
    return s


def _round_geom(g):
    def r(c):
        return [r(x) for x in c] if isinstance(c, list) and c and isinstance(c[0], list) else [round(v, 5) for v in c]
    if not g:
        return None
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def _centroid(g):
    pts = []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            pts.append(c)
        else:
            for x in c:
                walk(x)
    walk(g["coordinates"])
    if not pts:
        return None, None
    return sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts)


def _date(s):
    return (s or "")[:10] or None


def fetch():
    groups = {}
    for st in ("Government", "RealEstate"):
        for l in _listings(st):
            if l.get("listingType") != "SaleGroup":
                continue  # individual Lots under RealEstate are private broker listings
            title = clean(l.get("title"))
            interests = " ".join(l.get("interestTypes") or [])
            if SKIP_RE.search(title) or (interests and SKIP_RE.search(interests) and "Real Estate" not in interests):
                continue
            groups[l["id"]] = l

    out = []
    for gid, g in groups.items():
        sg = _get(f"SaleGroup/{gid}")
        try:
            gj = _get(f"geojson/salegroups/{gid}")
            geoms = {f["properties"]["id"]: f["geometry"] for f in gj.get("features", []) if f.get("geometry")}
        except Exception:
            geoms = {}
        gname = clean(sg.get("name") or g.get("title"))
        seller = (sg.get("seller") or g.get("seller") or {}).get("company", "") or sg.get("agencyName", "")
        for lot in sg.get("lots", []):
            props = (lot.get("lotProperties") or [{}])[0]
            st = state_abbr(props.get("stateName") or "") or state_abbr((lot.get("location") or "").split(",")[-1])
            county = props.get("countyName") or (lot.get("location") or "").split(",")[0]
            county = clean(county)
            if county and "County" not in county and "Parish" not in county:
                county += " County"
            m = re.search(r"([\d,]+(?:\.\d+)?)", lot.get("acreage") or "")
            acres = float(m.group(1).replace(",", "")) if m else None
            rule = lot.get("rule") or {}
            bid = lot.get("startingBid") or None
            price, ptype = None, None
            if bid:
                if rule.get("isPerUnit") and (rule.get("unitLabel") or "").lower() == "acre" and acres:
                    price, ptype = bid * acres, "Starting bid ($/ac × acres)"
                else:
                    price, ptype = float(bid), "Starting bid"
            cur = lot.get("currentBid") or lot.get("highBid") or lot.get("currentHighBid")
            geom = _round_geom(geoms.get(lot["id"]))
            lat, lon = _centroid(geom) if geom else (None, None)
            closing = _date(lot.get("closingDate") or lot.get("biddingEndDate"))
            status = lot.get("status") or sg.get("status")
            stage = {"Active": "Open for bidding", "Preview": "Upcoming", "Upcoming": "Upcoming",
                     "Closed": "Sale ended", "Complete": "Sale ended"}.get(status, status)
            if status == "Active" and lot.get("openingDate", "") > dt.datetime.utcnow().isoformat():
                stage = "Upcoming"
            out.append(Listing(
                source="energynet", source_id=str(lot["id"]),
                title=clean(lot.get("title") or lot.get("interestDescription") or gname),
                url=DETAIL.format(lot["id"]), agency=_agency(seller),
                state=st, county=county or None, acres=acres,
                price=price, price_type=ptype,
                sale_type={"Auction": "Online auction", "SealedBid": "Sealed bid",
                           "RequestForProposal": "Request for proposal"}.get(lot.get("auctionType"), lot.get("auctionType")),
                stage=stage, sale_date=closing, published=_date(g.get("listingStartDate")),
                summary=clean(lot.get("legalDescription"))[:500],
                lat=lat, lon=lon, loc_precision="parcel" if lat else None,
                geometry=geom,
                extra={"sale_group": gname, "sale_group_url": GROUP.format(gid),
                       "current_bid": parse_money(f"${cur}") if isinstance(cur, str) else cur,
                       "total_cost_at_start": rule.get("totalCost")},
            ))
    return out

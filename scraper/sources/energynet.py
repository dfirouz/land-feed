"""EnergyNet / EfficientMarkets government listings: BLM land sales (e.g. SNPLMA, Lincoln County)
and state trust land auctions (e.g. Utah SITLA). Oil & gas *lease* sales are skipped."""
import re

from ..browser import capture, walk_records
from ..common import Listing, clean, parse_acres, parse_county_state, parse_money, state_abbr

INDEX = "https://www.energynet.com/govt_listing.pl"
LAND_RE = re.compile(r"land\s+(sale|auction)|real estate|surface", re.I)
SKIP_RE = re.compile(r"lease|oil|gas|mineral|geothermal|coal", re.I)


def _norm(rec):
    return {re.sub(r"[^a-z]", "", k.lower()): v for k, v in rec.items()}


def _first(n, *keys):
    for k in keys:
        v = n.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _agency(title):
    t = title.upper()
    if t.startswith("BLM"):
        return "BLM"
    for k, v in {"SITLA": "Utah SITLA", "STATE LAND": "State land office", "GLO": "Texas GLO"}.items():
        if k in t:
            return v
    return title.split(" ")[0]


def _sale_groups(cap):
    groups = {}
    for pl in cap["json"]:
        for rec in walk_records(pl["data"]):
            n = _norm(rec)
            title = _first(n, "title", "name", "salegroupname", "description", "salename")
            sg = _first(n, "sg", "sgid", "salegroupid", "id")
            if isinstance(title, str) and sg and LAND_RE.search(title) and not SKIP_RE.search(title):
                groups[str(sg)] = {"title": clean(title), "rec": rec}
    # Fallback: links in rendered page
    for l in cap["links"]:
        m = re.search(r"(?:sg=|salegroup/)(\d+)", l["href"])
        if m and LAND_RE.search(l["text"] or "") and not SKIP_RE.search(l["text"] or ""):
            groups.setdefault(m.group(1), {"title": clean(l["text"]), "rec": {}})
    # Fallback: page text blocks "<Title>\n\nN Parcels covering X acres ..."
    return groups


def fetch():
    idx = capture("energynet_index", INDEX)
    groups = _sale_groups(idx)
    out = []
    for sg, g in list(groups.items())[:15]:
        url = f"https://www.energynet.com/govt_listing.pl?sg={sg}"
        title = g["title"]
        try:
            cap = capture(f"energynet_sg{sg}", url, wait_ms=4000)
        except Exception as e:
            print(f"  energynet sg {sg}: {e}")
            cap = {"json": [], "text": "", "links": []}
        text = cap["text"]
        county_g, st_g = parse_county_state(text[:5000])
        st_g = st_g or next((state_abbr(w) for w in re.findall(r"BLM\s+(\w+)|^(\w+)\s+SITLA", title) if w and state_abbr(w)), None)
        if "Utah" in title or "SITLA" in title:
            st_g = st_g or "UT"
        if "Nevada" in title or "SNPLMA" in title:
            st_g = st_g or "NV"
        status = "Open for bidding" if re.search(r"open for bidding", text, re.I) else (
            "Sale ended" if re.search(r"sale has ended|COMPLETE", text) else "Upcoming")
        lots = 0
        for pl in cap["json"]:
            for rec in walk_records(pl["data"]):
                n = _norm(rec)
                acres = _first(n, "acres", "acreage", "grossacres", "netacres")
                lot = _first(n, "lotid", "lot", "parcelid", "parcel", "tract", "lotnumber")
                if acres is None or lot is None:
                    continue
                try:
                    acres = float(str(acres).replace(",", ""))
                except ValueError:
                    continue
                price = _first(n, "minimumbid", "minbid", "fmv", "appraisedvalue", "currentbid", "highbid", "startingbid")
                price = float(price) if isinstance(price, (int, float)) else parse_money("$" + str(price)) if price else None
                county = _first(n, "county", "countystring", "countyname")
                legal = clean(str(_first(n, "legaldescription", "legal", "description") or ""))
                lots += 1
                out.append(Listing(
                    source="energynet", source_id=f"{sg}:{lot}",
                    title=f"{title} — Parcel {lot}", url=url, agency=_agency(title),
                    state=st_g, county=clean(str(county)) + ("" if not county or "County" in str(county) else " County") if county else county_g,
                    acres=acres, price=price,
                    price_type="Minimum bid / FMV" if price else None,
                    sale_type="Online auction", stage=status,
                    summary=legal[:400], extra={"sale_group": title, "apn": str(_first(n, "apn", "parcelnumber") or "")},
                ))
        if lots == 0:
            # Sale-group level row (parcel table not parsed yet)
            m = re.search(r"(\d[\d,]*)\s+Parcels?\s+(?:offered\s+)?covering\s+(?:approximately\s+)?([\d,.]+)\s+acres", text)
            out.append(Listing(
                source="energynet", source_id=sg, title=title, url=url, agency=_agency(title),
                state=st_g, county=county_g,
                acres=float(m.group(2).replace(",", "")) if m else parse_acres(text[:3000]),
                sale_type="Online auction", stage=status,
                summary=(f"{m.group(1)} parcels. " if m else "") + clean(text[:300]),
            ))
    return out

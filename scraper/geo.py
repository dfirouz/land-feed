"""Approximate geocoding from Census Gazetteer files (county / place centroids).
Exact parcel geometry is a later upgrade (e.g. Regrid or sale shapefiles)."""
import csv
import io
import re
import zipfile
from pathlib import Path

from .common import SESSION

CACHE = Path("cache/geo")
YEAR = "2024"
FILES = {
    "counties": f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{YEAR}_Gazetteer/{YEAR}_Gaz_counties_national.zip",
    "places": f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{YEAR}_Gazetteer/{YEAR}_Gaz_place_national.zip",
    "zcta": f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{YEAR}_Gazetteer/{YEAR}_Gaz_zcta_national.zip",
}
STATE_CENTROIDS = {}  # filled from counties (mean of county centroids)


def _load(kind):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{kind}.txt"
    if not p.exists():
        r = SESSION.get(FILES[kind], timeout=120)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        p.write_bytes(z.read(z.namelist()[0]))
    rows = list(csv.DictReader(io.StringIO(p.read_text(encoding="latin-1")), delimiter="\t"))
    return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in rows]


def _key(s):
    s = s.lower()
    s = re.sub(r"\b(county|parish|borough|city and borough|census area|municipality|city|town|village|cdp)\b", "", s)
    return re.sub(r"[^a-z]", "", s)


class Geocoder:
    def __init__(self):
        self.county, self.place, self.zcta = {}, {}, {}
        sums = {}
        try:
            for r in _load("counties"):
                ll = (float(r["INTPTLAT"]), float(r["INTPTLONG"]))
                self.county[(r["USPS"], _key(r["NAME"]))] = ll
                s = sums.setdefault(r["USPS"], [0, 0, 0])
                s[0] += ll[0]; s[1] += ll[1]; s[2] += 1
            for st, (a, b, n) in sums.items():
                STATE_CENTROIDS[st] = (a / n, b / n)
            for r in _load("places"):
                self.place.setdefault((r["USPS"], _key(r["NAME"])), (float(r["INTPTLAT"]), float(r["INTPTLONG"])))
            for r in _load("zcta"):
                self.zcta[r["GEOID"]] = (float(r["INTPTLAT"]), float(r["INTPTLONG"]))
        except Exception as e:
            print(f"geocoder: gazetteer load failed: {e}")

    def locate(self, l):
        if l.lat is not None:
            return
        z = (l.extra or {}).get("zip", "")[:5]
        if z and z in self.zcta:
            l.lat, l.lon = self.zcta[z]; l.loc_precision = "zip"; return
        if l.state and l.city and (l.state, _key(l.city)) in self.place:
            l.lat, l.lon = self.place[(l.state, _key(l.city))]; l.loc_precision = "city"; return
        if l.state and l.county:
            first = re.split(r"\s+and\s+", l.county)[0]
            ll = self.county.get((l.state, _key(first)))
            if ll:
                l.lat, l.lon = ll; l.loc_precision = "county"; return
        if l.state and l.state in STATE_CENTROIDS:
            l.lat, l.lon = STATE_CENTROIDS[l.state]; l.loc_precision = "state"

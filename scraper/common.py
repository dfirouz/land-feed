"""Shared helpers: listing schema, text parsing, HTTP."""
import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

UA = "land-feed/1.0 (public land listings monitor; github.com/dfirouz/land-feed)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico", "GU": "Guam", "VI": "Virgin Islands",
}
NAME_TO_ABBR = {v.lower(): k for k, v in STATES.items()}


def state_abbr(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = text.strip().rstrip(".").strip()
    if t.upper() in STATES:
        return t.upper()
    return NAME_TO_ABBR.get(t.lower())


@dataclass
class Listing:
    source: str                 # e.g. "federal_register", "gsa", "energynet"
    source_id: str              # stable id within the source
    title: str
    url: str
    agency: str = ""
    state: Optional[str] = None
    county: Optional[str] = None
    city: Optional[str] = None
    acres: Optional[float] = None
    price: Optional[float] = None
    price_type: Optional[str] = None   # "Minimum bid", "Starting bid", "FMV", "Asking"
    sale_type: Optional[str] = None    # "Competitive", "Modified competitive", "Direct", "Auction", ...
    stage: Optional[str] = None        # "Notice", "Bidding open", "Upcoming", "Sold", ...
    sale_date: Optional[str] = None
    comment_deadline: Optional[str] = None
    published: Optional[str] = None
    summary: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    loc_precision: Optional[str] = None  # "parcel", "zip", "city", "county", "state"
    geometry: Optional[dict] = None      # GeoJSON geometry when the source publishes parcel shapes
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return hashlib.sha1(f"{self.source}:{self.source_id}".encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["id"] = self.id
        d["price_per_acre"] = (
            round(self.price / self.acres, 2) if self.price and self.acres else None
        )
        return d


NUM = r"\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?"


def parse_money(text: str) -> Optional[float]:
    if not text:
        return None
    m = re.search(r"\$\s*(" + NUM + r")\s*(million|m\b)?", text, re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    if m.group(2):
        v *= 1_000_000
    return v


def parse_acres(text: str) -> Optional[float]:
    """Total acreage stated in text. Prefers 'totaling N acres' phrasing."""
    if not text:
        return None
    m = re.search(r"total(?:ing|s)?\s+(?:approximately\s+)?(" + NUM + r")\s*(?:\+/-\s*)?acres", text, re.I)
    if not m:
        m = re.search(r"(" + NUM + r")\s*(?:\+/-\s*|±\s*)?-?\s*acres?\b", text, re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_county_state(text: str):
    """Find '<Name> County, <State>' (or Parish/Borough). Returns (county, state_abbr)."""
    if not text:
        return None, None
    pat = (
        r"((?:[A-Z][\w.'-]*\s){1,3}(?:County|Counties|Parish|Borough))"
        r"(?:\s+and\s+(?:[A-Z][\w.'-]*\s){1,3}(?:County|Parish|Borough))?"
        r",?\s+(?:the\s+State\s+of\s+)?([A-Z][a-z]+(?:\s[A-Z][a-z]+)?|[A-Z]{2})\b"
    )
    for m in re.finditer(pat, text):
        st = state_abbr(m.group(2))
        if st:
            county = re.sub(r"^(?:in|of|and|the)\s+", "", m.group(1).strip(), flags=re.I)
            county = county.replace("Counties", "County")
            return county, st
    return None, None


def clean(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

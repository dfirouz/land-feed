"""Daily refresh: pull every source, geocode, diff against the last run, alert, write data files.

Usage: python -m scraper.run [--no-alerts] [--only gsa,energynet]
"""
import argparse
import csv
import datetime as dt
import json
import os
import traceback
from pathlib import Path

import requests

from .geo import Geocoder
from .sources import agencies, energynet, federal_register, gsa

SOURCES = {"federal_register": federal_register, "gsa": gsa, "energynet": energynet, "agencies": agencies}
SHORT = {"federal_register": "BLM notice", "gsa": "GSA", "energynet": "EfficientMarkets"}


def classify(d):
    """Top-level level/category/source_name used by the map filters and alerts."""
    ex = d.get("extra") or {}
    src = d["source"]
    if src.startswith("agency:"):
        d["level"], d["category"], d["source_name"] = ex.get("level"), ex.get("category", "surplus"), ex.get("source_name")
    else:
        d["level"] = "federal" if d.get("agency") in ("BLM", "GSA") else "state"
        d["category"], d["source_name"] = "surplus", SHORT.get(src, src)
    return d
DATA = Path("docs/data")
LISTINGS = DATA / "listings.json"
CHANGES = DATA / "changes.json"
CONFIG = Path("alerts.json")
TRACKED = ["price", "sale_date", "stage", "acres", "comment_deadline"]


def load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def matches(l, f):
    if l.get("category") == "tax_sale" and not f.get("include_tax_sales"):
        return False
    if f.get("levels") and l.get("level") not in f["levels"]:
        return False
    if f.get("states") and l.get("state") not in f["states"]:
        return False
    if f.get("sources") and l.get("source") not in f["sources"]:
        return False
    if f.get("min_acres") and (l.get("acres") or 0) < f["min_acres"]:
        return False
    if f.get("max_price") and l.get("price") and l["price"] > f["max_price"]:
        return False
    if f.get("max_price_per_acre") and l.get("price_per_acre") and l["price_per_acre"] > f["max_price_per_acre"]:
        return False
    if f.get("exclude_direct_sales") and "direct" in (l.get("sale_type") or "").lower():
        return False
    return True


def fmt_money(v):
    if v is None:
        return "n/a"
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v:,.0f}"


def notify(events, cfg, site_url):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC not set; skipping push alerts")
        return
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    filters = cfg.get("filters") or [{}]
    hits = [e for e in events if any(matches(e["listing"], f) for f in filters)]
    if not hits:
        return

    def send(title, body, click):
        try:
            requests.post(f"{server}/{topic}", data=body.encode(), timeout=30,
                          headers={"Title": title.encode("ascii", "ignore").decode(), "Click": click, "Tags": "world_map"})
        except Exception as e:
            print(f"ntfy failed: {e}")

    if len(hits) > 8:
        lines = [f"- {e['kind']}: {e['listing']['title'][:70]} ({e['listing'].get('state') or '?'})" for e in hits[:15]]
        send(f"{len(hits)} land listing updates", "\n".join(lines), site_url)
        return
    for e in hits:
        l = e["listing"]
        parts = [l.get("state") or "", f"{l['acres']:,.1f} ac" if l.get("acres") else "",
                 fmt_money(l.get("price")) if l.get("price") else "",
                 f"{fmt_money(l['price_per_acre'])}/ac" if l.get("price_per_acre") else "",
                 f"sale {l['sale_date']}" if l.get("sale_date") else ""]
        body = " · ".join(p for p in parts if p)
        if e["kind"] != "New":
            body = f"{e['detail']}\n{body}"
        send(f"{e['kind']}: {l['title'][:90]}", body or l["title"], l["url"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-alerts", action="store_true")
    ap.add_argument("--only", default="")
    a = ap.parse_args()

    today = dt.date.today().isoformat()
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
    prev = load(LISTINGS, {"listings": []})
    prev_by_id = {l["id"]: l for l in prev.get("listings", [])}
    first_run = not prev_by_id

    geo = Geocoder()
    fresh, ok_sources, status = {}, set(), {}
    for name, mod in SOURCES.items():
        if a.only and name not in a.only.split(","):
            continue
        try:
            items = mod.fetch()
            for l in items:
                geo.locate(l)
                d = classify(l.to_dict())
                fresh[d["id"]] = d
            status[name] = {"ok": True, "count": len(items), "at": now}
            if getattr(mod, "STATUS", None):
                status[name]["agencies"] = mod.STATUS
            if getattr(mod, "OK_SOURCES", None) is not None:
                ok_sources |= mod.OK_SOURCES
            elif items:
                ok_sources.add(name)
            print(f"{name}: {len(items)} listings")
        except Exception as e:
            traceback.print_exc()
            status[name] = {"ok": False, "error": str(e)[:300], "at": now}

    events, merged = [], {}
    for i, d in fresh.items():
        old = prev_by_id.get(i)
        d["first_seen"] = old.get("first_seen", today) if old else today
        d["last_seen"] = today
        d["removed"] = False
        if not old:
            events.append({"kind": "New", "detail": "", "listing": d, "date": today})
        else:
            diffs = [f"{k}: {old.get(k) or '—'} → {d.get(k) or '—'}" for k in TRACKED if old.get(k) != d.get(k) and d.get(k) is not None]
            if diffs:
                kind = "Price change" if any(x.startswith("price") for x in diffs) else "Updated"
                events.append({"kind": kind, "detail": "; ".join(diffs), "listing": d, "date": today})
        merged[i] = d
    for i, old in prev_by_id.items():
        if i in merged:
            continue
        if old["source"] in ok_sources and not old.get("removed"):
            old = {**old, "removed": True}
            events.append({"kind": "Removed", "detail": "No longer listed", "listing": old, "date": today})
        merged[i] = classify(old) if "level" not in old else old

    listings = sorted(merged.values(), key=lambda x: x.get("first_seen") or "", reverse=True)
    listings.sort(key=lambda x: x.get("removed", False))  # stable: active first, newest first
    prev_status = prev.get("sources", {})
    for k, v in prev_status.items():
        status.setdefault(k, v)

    DATA.mkdir(parents=True, exist_ok=True)
    LISTINGS.write_text(json.dumps({"updated": now, "sources": status, "listings": listings}, indent=1))
    log = load(CHANGES, [])
    log = ([{"date": e["date"], "kind": e["kind"], "detail": e["detail"], "id": e["listing"]["id"],
             "title": e["listing"]["title"], "state": e["listing"].get("state"), "url": e["listing"]["url"]}
            for e in events] + log)[:1000]
    CHANGES.write_text(json.dumps(log, indent=1))
    cols = ["id", "source", "source_name", "level", "category", "agency", "title", "state", "county", "city", "acres", "price", "price_type",
            "price_per_acre", "sale_type", "stage", "sale_date", "comment_deadline", "published",
            "first_seen", "last_seen", "removed", "url", "lat", "lon", "loc_precision"]
    with open(DATA / "listings.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(listings)

    print(f"{len(fresh)} fresh / {len(listings)} total / {len(events)} events")
    if first_run:
        print("First run: seeding, no alerts sent")
    elif not a.no_alerts:
        repo = os.environ.get("GITHUB_REPOSITORY", "dfirouz/land-feed")
        owner, name = repo.split("/")
        notify(events, load(CONFIG, {}), f"https://{owner}.github.io/{name}/")


if __name__ == "__main__":
    main()

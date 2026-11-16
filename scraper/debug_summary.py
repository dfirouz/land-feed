"""Print a compact summary of browser captures to the job log (artifacts aren't always reachable)."""
import json, sys
from pathlib import Path

root = Path("debug")
for d in sorted(p for p in root.glob("*") if p.is_dir()):
    print(f"\n======== {d.name} ========")
    txt = (d / "page.txt").read_text() if (d / "page.txt").exists() else ""
    print("--- text (first 2500 chars) ---")
    print(txt[:2500])
    html = (d / "page.html").read_text() if (d / "page.html").exists() else ""
    if d.name == "gsa" and html:
        i = html.find("NOW BIDDING")
        i = i if i > 0 else html.find("COMING SOON")
        print("--- html around first card ---")
        print(html[max(0, i - 3000): i + 3000])
    links = json.loads((d / "links.json").read_text()) if (d / "links.json").exists() else []
    print(f"--- links ({len(links)}) ---")
    for l in links[:80]:
        print(f"  {l['text'][:60]!r} -> {l['href'][:140]}")
    for j in sorted(d.glob("json_*.json")):
        raw = j.read_text()
        try:
            pl = json.loads(raw); url, s = pl["url"], json.dumps(pl["data"])
        except Exception:
            url, s = j.name, raw
        if any(x in url for x in ("monitor.azure", "analytics", "config.1.cfg")):
            continue
        print(f"--- json {url[:200]} ({len(s)} chars)")
        print("   " + s[:2500])

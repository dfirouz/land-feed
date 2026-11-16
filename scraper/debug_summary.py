"""Print a compact summary of browser captures to the job log (artifacts aren't always reachable)."""
import json, sys
from pathlib import Path

root = Path("debug")
for d in sorted(p for p in root.glob("*") if p.is_dir()):
    print(f"\n======== {d.name} ========")
    txt = (d / "page.txt").read_text() if (d / "page.txt").exists() else ""
    print("--- text (first 2500 chars) ---")
    print(txt[:2500])
    links = json.loads((d / "links.json").read_text()) if (d / "links.json").exists() else []
    print(f"--- links ({len(links)}) ---")
    for l in links[:80]:
        print(f"  {l['text'][:60]!r} -> {l['href'][:140]}")
    for j in sorted(d.glob("json_*.json")):
        pl = json.loads(j.read_text())
        s = json.dumps(pl["data"])
        print(f"--- json {pl['url'][:160]} ({len(s)} chars)")
        print("   " + s[:1200])

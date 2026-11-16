"""Headless-browser capture for JS-heavy sites. Saves every JSON response + final HTML
to debug/<name>/ so parsers can be written/fixed from real payloads."""
import json
import os
import re
from pathlib import Path

DEBUG = Path(os.environ.get("DEBUG_DIR", "debug"))


def capture(name, url, wait_ms=6000, scroll=True, clicks=()):
    from playwright.sync_api import sync_playwright

    out = DEBUG / name
    out.mkdir(parents=True, exist_ok=True)
    payloads = []

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"))

        def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                try:
                    data = resp.json()
                except Exception:
                    return
                payloads.append({"url": resp.url, "data": data})

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
        except Exception:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        for sel in clicks:
            try:
                page.click(sel, timeout=5000)
                page.wait_for_timeout(2500)
            except Exception:
                pass
        if scroll:
            for _ in range(8):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(700)
        page.wait_for_timeout(wait_ms)
        html = page.content()
        print(f"  [{name}] final url: {page.url}")
        links = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => ({href: e.href, text: e.innerText.trim().slice(0,200)}))")
        text = page.inner_text("body")
        b.close()

    (out / "page.html").write_text(html)
    (out / "page.txt").write_text(text)
    (out / "links.json").write_text(json.dumps(links, indent=1))
    for i, pl in enumerate(payloads):
        safe = re.sub(r"[^\w.-]+", "_", pl["url"])[-120:]
        (out / f"json_{i:02d}_{safe}.json").write_text(json.dumps(pl, indent=1))
    return {"html": html, "text": text, "links": links, "json": payloads}


def walk_records(obj):
    """Yield every dict that sits inside a list (likely a record) in a JSON tree."""
    if isinstance(obj, list):
        for x in obj:
            if isinstance(x, dict):
                yield x
            yield from walk_records(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_records(v)

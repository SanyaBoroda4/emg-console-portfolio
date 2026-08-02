"""Materials catalog refresher (slab scans chapter).

Runs on a GitHub Actions schedule:
  MODE=daily   -> Airtable materials table only (what we actually bought)
  MODE=weekly  -> Airtable + every supplier website

Each source is harvested and POSTed to the console's intake door
(/api/materials/upsert, X-Pilot-Secret). Everything is idempotent: the
catalog dedupes on lowercase name, so re-running is always safe.

Supplier harvesting strategies (per-site `kind`):
  stoneprofits  StoneProfits WebConnect sites load their ENTIRE inventory
                as one JSON call (getInventoryGallery) — capture it with a
                real browser and read ItemName. Pagination is display-only.
  shopify       /collections/<x>/products.json pages until empty.
  browser+ai    Load the page in a browser, scroll / click load-more until
                it stops growing, then let Claude extract the stone names
                from the page text. Survives redesigns; costs ~1 small AI
                call per site per week.

ENV: CONSOLE_URL, PILOT_HOOK_SECRET, AIRTABLE_TOKEN, ANTHROPIC_API_KEY, MODE
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

import httpx

CONSOLE_URL = os.environ.get(
    "CONSOLE_URL",
    "https://<app-name>.azurewebsites.net",
)
PILOT_HOOK_SECRET = os.environ["PILOT_HOOK_SECRET"]
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODE = os.environ.get("MODE", "weekly")

AIRTABLE_BASE = "appXXXXXXXXXXXXXX"
AIRTABLE_TABLE = "tblIgskDn26ykBEok"
AIRTABLE_VIEW = "viw2TlL9OcsWvRBW9"

# One entry per supplier page. 'kind' picks the harvesting strategy.
SUPPLIER_SITES: list[dict] = [
    {"supplier": "AGM Imports", "kind": "stoneprofits",
     "url": "https://inventory.agmimports.com/"},
    {"supplier": "Vitoria International", "kind": "stoneprofits",
     "url": "https://vitoria.stoneprofitsweb.com/"},
    {"supplier": "Easy Stones", "kind": "stoneprofits",
     "url": "https://easystones.stoneprofitsweb.com/"},
    {"supplier": "Cambria", "kind": "browser+ai",
     "url": "https://www.cambriausa.com/countertop-colors", "scroll": True},
    {"supplier": "Encore Stone Studio", "kind": "browser+ai",
     "url": "https://shop.encorestonestudio.com/", "scroll": True},
    {"supplier": "Bottega Surfaces", "kind": "browser+ai",
     "url": "https://bottegasurfaces.com/inventory/", "scroll": True},
    {"supplier": "Triton Stone", "kind": "browser+ai",
     "url": "https://tritonstone.com/inventory/?ItemTypes=SLAB", "scroll": True},
    {"supplier": "Cosmos Surfaces", "kind": "browser+ai",
     "url": "https://www.cosmossurfaces.com/charleston/granite"},
    {"supplier": "Cosmos Surfaces", "kind": "browser+ai",
     "url": "https://www.cosmossurfaces.com/charleston/dolomite"},
    {"supplier": "Cosmos Surfaces", "kind": "browser+ai",
     "url": "https://www.cosmossurfaces.com/charleston/marble"},
    {"supplier": "Cosmos Surfaces", "kind": "browser+ai",
     "url": "https://www.cosmossurfaces.com/charleston/quartz"},
    {"supplier": "Cosmos Surfaces", "kind": "browser+ai",
     "url": "https://www.cosmossurfaces.com/charleston/quartzite"},
    {"supplier": "Cosmos Surfaces", "kind": "browser+ai",
     "url": "https://www.cosmossurfaces.com/charleston/soap-stone"},
    {"supplier": "Easy Stones", "kind": "browser+ai",
     "url": "https://easystones.com/products/", "scroll": True},
    {"supplier": "StoneBasyx", "kind": "browser+ai",
     "url": "https://live-inventory.stonebasyx.com/", "scroll": True},
    {"supplier": "CRS Marble & Granite", "kind": "browser+ai",
     "url": "https://crsgranite.com/stones/granite/"},
    {"supplier": "CRS Marble & Granite", "kind": "browser+ai",
     "url": "https://crsgranite.com/stones/marble/"},
    {"supplier": "CRS Marble & Granite", "kind": "browser+ai",
     "url": "https://crsgranite.com/stones/quartz/"},
    {"supplier": "CRS Marble & Granite", "kind": "browser+ai",
     "url": "https://crsgranite.com/stones/quartzite/"},
    {"supplier": "CRS Marble & Granite", "kind": "browser+ai",
     "url": "https://crsgranite.com/stones/soapstones/"},
    {"supplier": "TVS USA", "kind": "browser+ai",
     "url": "https://tvs-usa.com/products/", "scroll": True},
    {"supplier": "UMI Stone", "kind": "browser+ai",
     "url": "https://umistone.com/live-inventory/boston/", "scroll": True},
    {"supplier": "MSI Surfaces", "kind": "browser+ai",
     "url": "https://msisurfaces.com/site-search/?ctgy=slab",
     "scroll": True, "load_more": True},
    {"supplier": "ARC Surfaces", "kind": "browser+ai",
     "url": "https://arcsurfaces.com/live-inventory/newjersey/", "scroll": True},
    {"supplier": "Cosentino", "kind": "browser+ai",
     "url": "https://www.cosentino.com/usa/colors/", "scroll": True},
    {"supplier": "Daltile", "kind": "browser+ai",
     "url": "https://www.daltile.com/products/slab/one-quartz-marble-look",
     "scroll": True},
]

JUNK_RE = re.compile(
    r"^(home|about|contact|inventory|search|filter|login|cart|menu|granite|"
    r"marble|quartz|quartzite|dolomite|soapstone|porcelain|all|none|next|"
    r"previous|load more|show more|view|details)$",
    re.I,
)


def upsert(names: list[tuple[str, str | None]], source: str) -> dict:
    materials = [{"name": n, "supplier": s} for n, s in names]
    if not materials:
        return {"ok": True, "added": 0, "received": 0}
    r = httpx.post(
        CONSOLE_URL + "/api/materials/upsert",
        headers={"X-Pilot-Secret": PILOT_HOOK_SECRET},
        json={"source": source, "materials": materials},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def clean(name: str) -> str | None:
    n = " ".join(str(name or "").split()).strip(' "\'`-')
    if len(n) < 3 or len(n) > 120 or JUNK_RE.match(n):
        return None
    if not re.search(r"[a-zA-Z]{3}", n):
        return None
    return n


# --------------------------------------------------------------------------
def harvest_airtable() -> None:
    if not AIRTABLE_TOKEN:
        print("airtable: no token, skipped")
        return
    names: list[tuple[str, str | None]] = []
    offset = None
    while True:
        params = {"view": AIRTABLE_VIEW, "pageSize": 100}
        if offset:
            params["offset"] = offset
        r = httpx.get(
            f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}",
            headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"},
            params=params, timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for rec in data.get("records", []):
            fields = rec.get("fields", {})
            name = clean(fields.get("Material"))
            if name:
                names.append((name, fields.get("Supplier") or None))
        offset = data.get("offset")
        if not offset:
            break
        time.sleep(0.25)
    result = upsert(names, "airtable")
    print(f"airtable: {len(names)} rows -> added {result['added']}")


# --------------------------------------------------------------------------
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def harvest_shopify(site: dict) -> list[str]:
    names: set[str] = set()
    page = 1
    while page <= 20:
        for attempt in range(4):
            r = httpx.get(
                f"{site['url']}/collections/{site['collection']}/products.json",
                params={"limit": 250, "page": page},
                headers={"User-Agent": UA}, timeout=60,
            )
            if r.status_code == 429:  # rate limited — back off and retry
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        else:
            break
        products = r.json().get("products", [])
        if not products:
            break
        for p in products:
            n = clean(p.get("title"))
            if n:
                names.add(n)
        page += 1
        time.sleep(1.5)  # be polite; avoids the 429
    return sorted(names)


def harvest_stoneprofits(site: dict, browser) -> list[str]:
    """StoneProfits WebConnect: the app fetches its whole inventory with one
    getInventoryGallery call. Read that RESPONSE body as the page's own JS
    receives it — the feed is session/nonce-bound, so re-fetching the URL
    returns empty. Runs under a real (headed, via xvfb) browser in CI; the
    inventory call doesn't fire under headless-shell."""
    ctx = browser.new_context(user_agent=UA)
    page = ctx.new_page()
    holder: dict = {}

    def on_response(response):
        if "getInventoryGallery" in response.url and "body" not in holder:
            try:
                text = response.text()
                if text and text.strip():
                    holder["body"] = text
            except Exception:  # noqa: BLE001
                pass

    page.on("response", on_response)
    for load in range(2):  # one reload retry if the call is slow to fire
        page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
        for _ in range(120):  # up to 60s
            if "body" in holder:
                break
            page.wait_for_timeout(500)
        if "body" in holder:
            break
    ctx.close()
    if "body" not in holder:
        raise RuntimeError("inventory feed never delivered")
    data = json.loads(holder["body"])
    names = {clean(i.get("ItemName")) for i in data}
    return sorted(n for n in names if n)


def harvest_browser_ai(site: dict, browser) -> list[str]:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("no ANTHROPIC_API_KEY for browser+ai site")
    ctx = browser.new_context(user_agent=UA)
    page = ctx.new_page()
    page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)  # let async product grids render
    last_height = 0
    for _ in range(40 if site.get("scroll") else 1):
        if site.get("load_more"):
            for label in ("Load More", "LOAD MORE", "Show More", "load more"):
                try:
                    page.click(f"text={label}", timeout=1500)
                    page.wait_for_timeout(1500)
                    break
                except Exception:  # noqa: BLE001
                    pass
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(900)
        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            break
        last_height = height
    text = page.evaluate("document.body.innerText")[:150000]
    ctx.close()

    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"},
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 8000,
            "messages": [{"role": "user", "content": (
                "Below is the visible text of a stone supplier's inventory "
                "page. Extract every STONE PRODUCT NAME (slab/material names "
                "like 'Taj Mahal Quartzite' or 'Calacatta Gold 3cm'). Do NOT "
                "include navigation labels, plain category words, prices, "
                "sizes alone, or company text. Reply with ONLY the names, "
                "one per line, no numbering.\n\n=== PAGE TEXT ===\n" + text
            )}],
        },
        timeout=180,
    )
    r.raise_for_status()
    reply = "".join(b.get("text", "") for b in r.json().get("content", []))
    names = {clean(line) for line in reply.splitlines()}
    return sorted(n for n in names if n)


def harvest_suppliers() -> None:
    from playwright.sync_api import sync_playwright

    totals: dict[str, int] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        for site in SUPPLIER_SITES:
            label = f"{site['supplier']} ({site['url']})"
            try:
                if site["kind"] == "shopify":
                    names = harvest_shopify(site)
                elif site["kind"] == "stoneprofits":
                    names = harvest_stoneprofits(site, browser)
                else:
                    names = harvest_browser_ai(site, browser)
                result = upsert([(n, site["supplier"]) for n in names], "website")
                totals[site["supplier"]] = totals.get(site["supplier"], 0) + len(names)
                print(f"OK   {label}: {len(names)} names, added {result['added']}")
            except Exception as exc:  # noqa: BLE001 — one site must not kill the run
                print(f"FAIL {label}: {exc}")
        browser.close()
    print("supplier totals:", json.dumps(totals, indent=1))


if __name__ == "__main__":
    print(f"materials refresh MODE={MODE}")
    harvest_airtable()
    if MODE == "weekly":
        harvest_suppliers()
    print("done")
    sys.exit(0)

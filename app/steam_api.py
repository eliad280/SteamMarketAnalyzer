"""Thin client around the unofficial Steam Community endpoints used by this app.

No official public API key is required for market prices or public inventories.
Steam's market/inventory endpoints sit behind Akamai bot detection that flags
plain Python HTTP clients by their TLS/HTTP2 fingerprint even with "browser"
headers attached - a real browser from the same IP sails through while a
vanilla httpx client gets 429'd forever. curl_cffi impersonates a real
Chrome network fingerprint, which reliably gets past this. On top of that,
every call here goes through a small async throttle plus the SQLite cache
in app.db to stay well under whatever request-rate limits Steam does enforce.
"""
import asyncio
import json
import time
from pathlib import Path

from curl_cffi.requests import AsyncSession

from . import currency as cur
from . import db

IMPERSONATE = "chrome124"

BROWSER_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Anonymous requests to the market endpoints get 429'd well under 1 req/sec
# sustained. Keep a healthy gap between calls.
_MIN_INTERVAL = 2.0
_last_call_at = 0.0
_lock = asyncio.Lock()

CACHE_TTL_SECONDS = 15 * 60

# A small curated list of popular apps with a Steam Community Market.
POPULAR_GAMES = [
    {"appid": 730, "name": "Counter-Strike 2", "contextid": 2},
    {"appid": 570, "name": "Dota 2", "contextid": 2},
    {"appid": 440, "name": "Team Fortress 2", "contextid": 2},
    {"appid": 252490, "name": "Rust", "contextid": 2},
    {"appid": 304930, "name": "Unturned", "contextid": 2},
    {"appid": 578080, "name": "PUBG: BATTLEGROUNDS", "contextid": 2},
]


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


async def _throttle():
    global _last_call_at
    async with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = time.monotonic()


def _cookie_header() -> dict:
    cfg = _load_config()
    steam_login_secure = cfg.get("steamLoginSecure")
    sessionid = cfg.get("sessionid")
    if steam_login_secure and sessionid:
        return {"Cookie": f"steamLoginSecure={steam_login_secure}; sessionid={sessionid}"}
    return {}


def is_login_configured() -> bool:
    """Whether config.json has a Steam session cookie saved (via login_helper.py
    or manual DevTools copy). Doesn't verify the cookie is still valid - just
    that one is present."""
    return bool(_cookie_header())


async def get_price_overview(appid: int, market_hash_name: str, currency: str,
                              use_cache: bool = True) -> dict:
    """Returns {price: float|None, volume: int|None, raw_lowest, raw_median, cached: bool}."""
    if use_cache:
        cached = db.cache_get(appid, market_hash_name, currency, CACHE_TTL_SECONDS)
        if cached:
            cached["cached"] = True
            return cached

    await _throttle()
    params = {
        "appid": appid,
        "currency": cur.currency_id(currency),
        "market_hash_name": market_hash_name,
    }
    async with AsyncSession() as client:
        resp = await client.get(
            "https://steamcommunity.com/market/priceoverview/", params=params,
            headers=BROWSER_HEADERS, timeout=15, impersonate=IMPERSONATE,
        )
    if resp.status_code == 429:
        raise RuntimeError("Steam דחה את הבקשה עקב יותר מדי קריאות (429). נסה שוב בעוד דקה.")
    resp.raise_for_status()
    data = resp.json()

    price = cur.parse_price(data.get("lowest_price") or data.get("median_price"))
    volume_raw = data.get("volume")
    volume = int(volume_raw.replace(",", "")) if volume_raw else None

    db.cache_put(appid, market_hash_name, currency, price, volume,
                 data.get("lowest_price"), data.get("median_price"))
    if price is not None:
        db.add_snapshot(appid, market_hash_name, currency, price, volume)

    return {
        "price": price,
        "volume": volume,
        "raw_lowest": data.get("lowest_price"),
        "raw_median": data.get("median_price"),
        "cached": False,
    }


async def get_official_price_history(appid: int, market_hash_name: str, currency: str,
                                      reference_price: float | None = None) -> list[dict] | None:
    """Returns real Steam price history, but only works with a logged-in session cookie
    (configured in config.json). Returns None if no cookie is configured or the call fails.

    Quirk: this endpoint ignores the requested `currency` when authenticated and
    returns prices in the logged-in account's actual Steam wallet currency instead
    (e.g. an Israeli account gets ILS back even when currency=USD was requested).
    We detect and correct for this by calibrating against `priceoverview`, which
    does honor `currency` correctly, using the ratio at the most recent point.
    `reference_price` lets a caller that already fetched the current price (in
    the target currency) pass it in instead of triggering an extra request.
    """
    cookie_headers = _cookie_header()
    if not cookie_headers:
        return None
    await _throttle()
    params = {
        "appid": appid,
        "currency": cur.currency_id(currency),
        "market_hash_name": market_hash_name,
    }
    headers = {**BROWSER_HEADERS, **cookie_headers}
    # Steam sometimes 400s pricehistory calls that don't look like they came
    # from the actual item page, so pretend we do.
    from urllib.parse import quote
    headers["Referer"] = f"https://steamcommunity.com/market/listings/{appid}/{quote(market_hash_name)}"
    async with AsyncSession() as client:
        resp = await client.get(
            "https://steamcommunity.com/market/pricehistory/", params=params,
            headers=headers, timeout=15, impersonate=IMPERSONATE,
        )
    if resp.status_code != 200:
        return None
    data = resp.json()
    if not data.get("success"):
        return None
    out = []
    for date_str, price, volume in data.get("prices", []):
        # date_str looks like "Jan 01 2024 01: +0"
        try:
            ts = time.mktime(time.strptime(date_str.split(":")[0].strip(), "%b %d %Y %H"))
        except ValueError:
            continue
        out.append({"ts": int(ts), "price": float(price), "volume": int(float(volume))})

    if not out:
        return out

    if reference_price is None:
        live = await get_price_overview(appid, market_hash_name, currency)
        reference_price = live.get("price")

    last_raw_price = out[-1]["price"]
    if reference_price and last_raw_price > 0:
        scale = reference_price / last_raw_price
        # A small gap is just normal market movement between the last history
        # bucket and the live quote. A large one means pricehistory answered
        # in a different currency than requested - correct the whole series.
        if abs(scale - 1.0) > 0.15:
            for p in out:
                p["price"] = round(p["price"] * scale, 4)

    return out


async def resolve_steamid(steamid_or_vanity: str) -> str:
    """Accepts a 17-digit SteamID64 directly, or (if a STEAM_API_KEY is set in config.json)
    resolves a vanity profile name."""
    s = steamid_or_vanity.strip()
    if s.isdigit() and len(s) == 17:
        return s
    cfg = _load_config()
    api_key = cfg.get("steam_api_key")
    if not api_key:
        raise ValueError(
            "יש להזין SteamID64 בן 17 ספרות. פענוח שם פרופיל (vanity URL) דורש "
            "מפתח Steam Web API בקובץ config.json (steam_api_key)."
        )
    async with AsyncSession() as client:
        resp = await client.get(
            "https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/",
            params={"key": api_key, "vanityurl": s},
            headers=BROWSER_HEADERS, timeout=15, impersonate=IMPERSONATE,
        )
    resp.raise_for_status()
    data = resp.json().get("response", {})
    if data.get("success") != 1:
        raise ValueError("לא ניתן היה למצוא פרופיל Steam תואם.")
    return data["steamid"]


MAX_INVENTORY_PAGE = 2000  # Steam 400s the inventory endpoint above ~2000-3000.
MAX_INVENTORY_PAGES = 10   # safety cap: 20,000 items should cover any real inventory.


async def _fetch_inventory_page(steamid64: str, appid: int, contextid: int,
                                 start_assetid: str | None) -> dict:
    url = f"https://steamcommunity.com/inventory/{steamid64}/{appid}/{contextid}"
    # Note: deliberately anonymous, even if a login cookie is configured.
    # Steam's inventory endpoint serves public inventories fine without one,
    # and an attached session cookie has been observed to cause spurious 400s.
    headers = {
        **BROWSER_HEADERS,
        "Referer": f"https://steamcommunity.com/profiles/{steamid64}/inventory/",
    }
    params = {"l": "english", "count": MAX_INVENTORY_PAGE}
    if start_assetid:
        params["start_assetid"] = start_assetid

    await _throttle()
    async with AsyncSession() as client:
        resp = await client.get(url, params=params, headers=headers, timeout=20, impersonate=IMPERSONATE)

    if resp.status_code in (400, 403):
        # Steam returns 400 or 403 both for private inventories and for
        # profiles that simply have no items for this appid/context.
        return {"private": True}
    if resp.status_code == 429:
        raise RuntimeError("Steam דחה את הבקשה עקב יותר מדי קריאות (429). נסה שוב בעוד דקה.")
    resp.raise_for_status()
    return {"private": False, "data": resp.json()}


async def get_inventory(steamid64: str, appid: int, contextid: int = 2) -> dict:
    """Fetches a public inventory (paginating past Steam's ~2000-item-per-request
    cap when needed) and groups items by (classid, instanceid).

    Returns {"items": [...], "private": bool}
    """
    desc_map: dict[tuple, dict] = {}
    assets: list[dict] = []
    start_assetid = None

    for _ in range(MAX_INVENTORY_PAGES):
        page = await _fetch_inventory_page(steamid64, appid, contextid, start_assetid)
        if page["private"]:
            return {"items": [], "private": True}

        data = page["data"]
        if not data or not data.get("assets"):
            break

        for d in data.get("descriptions", []):
            key = (str(d.get("classid")), str(d.get("instanceid")))
            desc_map[key] = d
        assets.extend(data["assets"])

        if data.get("more_items"):
            start_assetid = str(data.get("last_assetid"))
        else:
            break

    groups: dict[tuple, dict] = {}
    for asset in assets:
        key = (str(asset.get("classid")), str(asset.get("instanceid")))
        desc = desc_map.get(key)
        if not desc:
            continue
        market_hash_name = desc.get("market_hash_name")
        if key not in groups:
            item_type = "לא ידוע"
            for tag in desc.get("tags", []):
                if tag.get("category") == "Type":
                    item_type = tag.get("localized_tag_name", item_type)
                    break
            groups[key] = {
                "market_hash_name": market_hash_name,
                "name": desc.get("name") or market_hash_name,
                "icon_url": desc.get("icon_url"),
                "type": item_type,
                "marketable": bool(desc.get("marketable")),
                "tradable": bool(desc.get("tradable")),
                "qty": 0,
            }
        groups[key]["qty"] += int(asset.get("amount", 1))

    return {"items": list(groups.values()), "private": False}

import asyncio
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db, prediction, steam_api

app = FastAPI(title="Steam Market Analyzer")

STATIC_DIR = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGIN_HELPER_SCRIPT = PROJECT_ROOT / "login_helper.py"

VALID_CURRENCIES = ("USD", "ILS")

_login_process: subprocess.Popen | None = None


def _check_currency(currency: str) -> str:
    c = currency.upper()
    if c not in VALID_CURRENCIES:
        raise HTTPException(400, f"מטבע לא נתמך: {currency}")
    return c


@app.on_event("startup")
async def startup():
    db.init_db()


@app.get("/api/games")
async def games():
    return steam_api.POPULAR_GAMES


@app.get("/api/currencies")
async def currencies():
    return VALID_CURRENCIES


@app.get("/api/login/status")
async def login_status():
    global _login_process
    in_progress = _login_process is not None and _login_process.poll() is None
    return {"configured": steam_api.is_login_configured(), "in_progress": in_progress}


@app.post("/api/login/start")
async def login_start():
    global _login_process
    if _login_process is not None and _login_process.poll() is None:
        return {"started": False, "reason": "already_running"}
    if not LOGIN_HELPER_SCRIPT.exists():
        raise HTTPException(500, "login_helper.py לא נמצא בתיקיית הפרויקט.")
    _login_process = subprocess.Popen(
        [sys.executable, str(LOGIN_HELPER_SCRIPT)], cwd=str(PROJECT_ROOT)
    )
    return {"started": True}


@app.get("/api/price")
async def price(appid: int, name: str, currency: str = "USD"):
    currency = _check_currency(currency)
    try:
        result = await steam_api.get_price_overview(appid, name, currency)
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    if result["price"] is None:
        raise HTTPException(404, "לא נמצא מחיר עבור פריט זה. ודא ששם הפריט מדויק.")
    return result


@app.get("/api/history")
async def history(appid: int, name: str, currency: str = "USD"):
    currency = _check_currency(currency)
    official = await steam_api.get_official_price_history(appid, name, currency)
    if official:
        return {"source": "official", "points": official}
    local = db.get_history(appid, name, currency)
    return {"source": "local", "points": local}


@app.get("/api/predict")
async def predict(appid: int, name: str, currency: str = "USD"):
    currency = _check_currency(currency)
    try:
        current = await steam_api.get_price_overview(appid, name, currency)
    except RuntimeError as e:
        raise HTTPException(429, str(e))

    official = await steam_api.get_official_price_history(appid, name, currency, reference_price=current["price"])
    hist_points = official if official else db.get_history(appid, name, currency)

    result = prediction.analyze(hist_points, current["price"], current["volume"])
    result["source"] = "official" if official else "local"
    result["item"] = name
    result["appid"] = appid
    result["currency"] = currency
    return result


@app.get("/api/inventory/list")
async def inventory_list(steamid: str, appid: int, contextid: int = 2):
    try:
        resolved = await steam_api.resolve_steamid(steamid)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        data = await steam_api.get_inventory(resolved, appid, contextid)
    except RuntimeError as e:
        raise HTTPException(429, str(e))
    if data["private"]:
        raise HTTPException(
            403,
            "לא ניתן היה לקרוא את ה-Inventory. ייתכן שהוא פרטי, שהפרופיל אינו ציבורי, "
            "או שאין למשתמש פריטים במשחק זה. ודא שהפרטיות מוגדרת כ-Public ונסה שוב.",
        )
    types = sorted({item["type"] for item in data["items"]})
    return {"items": data["items"], "types": types, "steamid": resolved}


@app.post("/api/inventory/evaluate")
async def inventory_evaluate(payload: dict):
    """payload: {appid, currency, items: [market_hash_name, ...]}"""
    appid = payload.get("appid")
    currency = _check_currency(payload.get("currency", "USD"))
    names = payload.get("items", [])
    if not appid or not names:
        raise HTTPException(400, "חסרים appid או items")
    if len(names) > 200:
        raise HTTPException(400, "יותר מדי פריטים בבקשה אחת (מקסימום 200). סנן לפני ההערכה.")

    results = {}
    for n in names:
        try:
            r = await steam_api.get_price_overview(appid, n, currency)
        except RuntimeError as e:
            results[n] = {"error": str(e)}
            continue
        official = await steam_api.get_official_price_history(appid, n, currency, reference_price=r["price"])
        hist = official if official else db.get_history(appid, n, currency)
        pred = prediction.analyze(hist, r["price"], r["volume"])
        results[n] = {
            "price": r["price"],
            "volume": r["volume"],
            "recommendation": pred.get("recommendation"),
            "confidence": pred.get("confidence"),
        }
    return results


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

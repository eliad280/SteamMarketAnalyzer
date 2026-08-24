"""One-time helper to enable real Steam price history.

Opens a native window with Steam's real login page (steamcommunity.com,
rendered by your system's Edge WebView2 - not a fake page). You log in
exactly as you normally would, including Steam Guard. Your password goes
directly to Steam and is never seen by this script or by the main app -
this only reads the session cookies your login leaves behind afterwards
(the same steamLoginSecure/sessionid values you could otherwise copy by
hand from your browser's DevTools) and saves them into config.json.

Run with: python login_helper.py
"""
import json
from pathlib import Path

import webview

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
LOGIN_URL = "https://steamcommunity.com/login/home/?goto="

_saved = False


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _on_loaded():
    global _saved
    if _saved:
        return

    window = webview.windows[0]
    raw_cookies = window.get_cookies()

    flat = {}
    for simple_cookie in raw_cookies:
        for name, morsel in simple_cookie.items():
            flat[name] = morsel.value

    steam_login_secure = flat.get("steamLoginSecure")
    sessionid = flat.get("sessionid")

    if not (steam_login_secure and sessionid):
        return  # not logged in yet - keep waiting for the next page load

    cfg = _load_config()
    cfg["steamLoginSecure"] = steam_login_secure
    cfg["sessionid"] = sessionid
    _save_config(cfg)
    _saved = True

    print("\nהתחברות הצליחה! הפרטים נשמרו ב-config.json.")
    print("סוגר את חלון ההתחברות - אפשר להריץ עכשיו את האפליקציה הראשית כרגיל.\n")
    window.destroy()


if __name__ == "__main__":
    print("נפתח חלון התחברות אמיתי של Steam. התחבר כרגיל (כולל Steam Guard אם נדרש).")
    print("החלון ייסגר אוטומטית ברגע שההתחברות תזוהה.\n")

    win = webview.create_window(
        "התחברות ל-Steam (חד פעמי, לצורך שליפת היסטוריית מחירים)",
        LOGIN_URL,
        width=520,
        height=720,
    )
    win.events.loaded += _on_loaded
    webview.start()

    if not _saved:
        print("החלון נסגר לפני שזוהתה התחברות מוצלחת. אפשר להריץ שוב את הסקריפט בכל עת.")

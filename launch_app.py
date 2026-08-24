"""One-click launcher: starts the FastAPI server in the background, opens it
in a native app window, and shuts the server down automatically when that
window is closed - so there's no leftover server process or terminal window
to manage by hand.

Run with: pythonw.exe launch_app.py  (see "Run Steam Market Analyzer.bat")
"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import webview

PROJECT_ROOT = Path(__file__).resolve().parent
PORT = 8000
URL = f"http://127.0.0.1:{PORT}"


def _wait_for_server(url: str, timeout: float = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    # Under pythonw.exe there is no console, so the parent's own
    # stdout/stderr are None - a child that inherits them (the default)
    # crashes or hangs the moment uvicorn's logging tries to write to them.
    # Redirect the child's streams explicitly instead of inheriting.
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )

    try:
        if not _wait_for_server(URL):
            webview.create_window("Steam Market Analyzer - שגיאה", html=(
                "<body style='font-family:sans-serif;padding:40px;direction:rtl'>"
                "<h2>השרת לא עלה בזמן</h2>"
                "<p>נסה להריץ ידנית: uvicorn app.main:app --port 8000</p>"
                "</body>"
            ))
            webview.start()
            return

        webview.create_window("Steam Market Analyzer", URL, width=1300, height=850, min_size=(900, 600))
        webview.start()
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()


if __name__ == "__main__":
    main()

"""Build the page for hosting: one index.html with the styles and the script inside, plus fonts and the Telegram script.

Usage: build_web.py <output dir>
One file means one request for the whole app shell; fonts are cached by the browser forever.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def build(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "app.css").read_text(encoding="utf-8")
    js = (WEB / "app.js").read_text(encoding="utf-8")
    html, styles = re.subn(r'<link rel="stylesheet" href="app\.css[^"]*">', lambda _m: f"<style>\n{css}</style>", html)
    html, scripts = re.subn(r'<script src="app\.js[^"]*"></script>', lambda _m: f"<script>\n{js}</script>", html)
    if (styles, scripts) != (1, 1) or "</script>" in js:
        raise SystemExit("index.html does not reference app.css and app.js exactly once, or app.js contains </script>")
    (out / "index.html").write_text(html, encoding="utf-8")
    shutil.copy(WEB / "telegram-web-app.js", out / "telegram-web-app.js")
    shutil.copytree(WEB / "fonts", out / "fonts", dirs_exist_ok=True)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
    print("built:", sys.argv[1])

"""Воспроизведение: окно Chrome закрыли руками, затем программа должна открыть его заново.
Работает на копии профиля dist/profiles/pavel, чтобы не мешать основному окну."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from okf import browser, paths  # noqa: E402

paths.PROFILES = paths.BASE / "dist" / "profiles"

with sync_playwright() as pw:
    ctx = browser.launch_context(pw, "Павел.")
    page = ctx.pages[0]
    print("1 opened:", page.url)
    # закрываем окно так же, как человек: закрываем все вкладки
    for p in list(ctx.pages):
        p.close()
    time.sleep(2)
    try:
        print("2 pages after close:", ctx.pages)
    except Exception as e:
        print("2 pages raised:", type(e).__name__, str(e)[:200])
    try:
        ctx.close()
        print("3 ctx.close ok")
    except Exception as e:
        print("3 ctx.close raised:", type(e).__name__, str(e)[:200])
    time.sleep(1)
    try:
        ctx2 = browser.launch_context(pw, "Павел.")
        page2 = ctx2.pages[0]
        print("4 relaunched, pages:", len(ctx2.pages), "url:", page2.url)
        ok = browser.is_logged_in(page2)
        print("5 logged in:", ok, "url:", page2.url)
        ctx2.close()
    except Exception as e:
        print("4/5 raised:", type(e).__name__, str(e)[:400])

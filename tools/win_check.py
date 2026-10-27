"""Окно Chrome программы свёрнуто или перекрыто другим окном: всплывает ли меню друга по наведению.
Запускается на макете ОК (tools/mock_ok.py), профиль во временной папке. Для GitHub Actions на Windows
(.github/workflows/windows-check.yml) и для мака: python tools/win_check.py"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("OKF_BASE", tempfile.mkdtemp(prefix="okf_win_"))
os.environ["OKF_OK_URL"] = "http://127.0.0.1:8766"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import mock_ok  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from okf import browser, modes, paths  # noqa: E402

FAILS = []


def hover(page, label):
    page.goto("http://127.0.0.1:8766/profile/1/friends", wait_until="load")
    page.wait_for_timeout(500)
    got = []
    for uid in ("2002", "2003", "2005"):
        b = page.locator(f".user-grid-card[data-id='{uid}'] .user-grid-card_img").bounding_box()
        page.mouse.move(5, 5)
        page.mouse.move(b["x"] + 60, b["y"] + 60, steps=4)
        try:
            # меню в макете с v0.2.2 — как на ОК: не в карточке, а новое в общем #hook_Block_ShortcutMenu
            page.locator(f"#hook_Block_ShortcutMenu .sc-menu[data-uid='{uid}']:not(.sc-menu__hidden) a.u-menu_a").first.wait_for(
                state="visible", timeout=3000)
            got.append(True)
        except Exception:
            got.append(False)
    vs = page.evaluate("document.visibilityState")
    print(f"{label}: visibilityState={vs}, меню всплыло {sum(got)} из 3", flush=True)
    return vs, all(got)


def timers(page):
    ms = page.evaluate("() => new Promise(r => { const t0 = performance.now(); let n = 0;"
                       " const f = () => { if (++n < 20) setTimeout(f, 50); else r(performance.now() - t0); }; f(); })")
    print(f"  20 таймеров по 50 мс: {ms:.0f} мс", flush=True)


def window_state(cdp, wid):
    return cdp.send("Browser.getWindowBounds", {"windowId": wid})["bounds"].get("windowState")


class FakeDrv:
    def log(self, mode, text):
        print("  журнал программы:", text, flush=True)


def main():
    paths.ensure_dirs()
    mock_ok.serve(8766)
    with sync_playwright() as pw:
        ctx = browser.launch_context(pw, "win-check")
        page = ctx.pages[0]
        cdp = ctx.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]

        vs, ok = hover(page, "1) обычное окно")
        if not ok:
            FAILS.append("обычное окно")

        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
        time.sleep(2)
        print("2) окно свёрнуто:", window_state(cdp, wid), flush=True)
        vs, ok = hover(page, "2) свёрнутое окно, без разворачивания")
        timers(page)
        print("   итог: свёрнутое окно", "НЕ мешает наведению" if ok else "ЛОМАЕТ наведение", flush=True)

        # как в программе: Session.prep() видит hidden и разворачивает окно (Session.restore_window)
        s = object.__new__(modes.Session)
        s.page, s.drv, s.mode, s.stop = page, FakeDrv(), "delete", threading.Event()
        s.prep()
        time.sleep(1)
        print("3) после Session.prep():", window_state(cdp, wid), flush=True)
        vs, ok = hover(page, "3) после prep()")
        if not ok:
            FAILS.append("после prep() у свёрнутого окна")

        # перекрыто другим окном: поверх — новое окно Chrome во весь экран
        page.evaluate("() => { window.okfCover = window.open('about:blank', 'cover', 'popup,left=0,top=0,width=1600,height=1100'); }")
        time.sleep(2)
        vs, ok = hover(page, "4) окно перекрыто другим окном")
        timers(page)
        if not ok:
            FAILS.append("перекрытое окно")
        try:
            page.evaluate("() => window.okfCover && window.okfCover.close()")
        except Exception:
            pass
        ctx.close()
    print("ИТОГО:", "все проверки прошли" if not FAILS else "не прошли: " + ", ".join(FAILS), flush=True)
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

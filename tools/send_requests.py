"""Разово: отправить заявки в друзья с аккаунта программы указанным профилям (для подготовки проверок).
Использование: python tools/send_requests.py <id1> <id2> ...
"""
import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from okf import browser  # noqa: E402
from okf.human import Mouse  # noqa: E402

ACCOUNT = "Павел."


def main():
    ids = sys.argv[1:]
    with sync_playwright() as pw:
        ctx = browser.launch_context(pw, ACCOUNT)
        page = ctx.pages[0]
        mouse = Mouse(page, threading.Event())
        for i, uid in enumerate(ids):
            page.goto(f"https://ok.ru/profile/{uid}", wait_until="domcontentloaded", timeout=60000)
            time.sleep(random.uniform(2, 4))
            name = page.locator(".profile-user-info_name").first.inner_text(timeout=5000).strip()
            mouse.scroll(random.randint(150, 400))
            time.sleep(random.uniform(1, 2))
            mouse.scroll(-random.randint(150, 400))
            btn = page.locator("#hook_Block_MainMenu a.u-menu_a:has-text('Добавить в друзья')")
            if btn.count() == 0 or not btn.first.is_visible():
                txt = page.locator("#hook_Block_MainMenu").inner_text(timeout=3000).replace("\n", " / ")
                print(f"{uid} {name}: кнопки нет, в меню: {txt[:120]}")
                continue
            mouse.click(btn.first)
            time.sleep(random.uniform(2.5, 4))
            menu = page.locator("#hook_Block_MainMenu").inner_text(timeout=3000).replace("\xa0", " ")
            sent = ("Заявка отправлена" in menu) or ("Отменить заявку" in menu) or \
                   (page.locator("#hook_Block_MainMenu a.u-menu_a:has-text('Добавить в друзья')").count() == 0)
            print(f"{uid} {name}: {'заявка отправлена' if sent else 'НЕ подтверждено'} | меню: {menu.replace(chr(10), ' / ')[:100]}")
            if i < len(ids) - 1:
                time.sleep(random.uniform(8, 20))
        ctx.close()


if __name__ == "__main__":
    main()

"""Разведка: открыть профиль аккаунта тем же способом, что программа, и выполнить команду.

Использование:
  python tools/explore.py sannysoft          — bot.sannysoft.com, скриншот всей страницы
  python tools/explore.py open <url> <png>   — открыть адрес, скриншот, вывести ссылки/кнопки
  python tools/explore.py dump <url> <css>   — вывести outerHTML первых элементов по селектору
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402
from okf import browser, paths  # noqa: E402

ACCOUNT = "Павел."
OUT = paths.SCREENSHOTS


def main():
    cmd = sys.argv[1]
    paths.ensure_dirs()
    with sync_playwright() as pw:
        ctx = browser.launch_context(pw, ACCOUNT)
        page = ctx.pages[0]
        try:
            if cmd == "sannysoft":
                page.goto("https://bot.sannysoft.com/", wait_until="load", timeout=60000)
                page.wait_for_timeout(4000)
                page.screenshot(path=str(OUT / "sannysoft.png"), full_page=True)
                rows = page.locator("table tr").all_inner_texts()
                for r in rows[:40]:
                    print(" | ".join(x.strip() for x in r.split("\n") if x.strip()))
            elif cmd == "open":
                url, png = sys.argv[2], sys.argv[3]
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                print("URL:", page.url)
                print("TITLE:", page.title())
                page.screenshot(path=str(OUT / png), full_page=False)
                links = page.evaluate("""() => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => [a.getAttribute('href'), (a.innerText||'').trim().slice(0,60)])
                    .filter(x => x[0] && x[1])""")
                seen = set()
                for href, text in links:
                    if href in seen:
                        continue
                    seen.add(href)
                    print("A", href, "|", text.replace("\n", " "))
            elif cmd == "eval":
                page.goto(sys.argv[2], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                print(page.evaluate(sys.argv[3]))
            elif cmd == "dump":
                url, css = sys.argv[2], sys.argv[3]
                limit = int(sys.argv[4]) if len(sys.argv) > 4 else 3
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                print("URL:", page.url)
                els = page.locator(css)
                print("COUNT:", els.count())
                for i in range(min(limit, els.count())):
                    html = els.nth(i).evaluate("e => e.outerHTML")
                    print(f"----- [{i}] -----")
                    print(html[:6000])
        finally:
            ctx.close()


if __name__ == "__main__":
    main()

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
            elif cmd == "mark":
                # mark <url> <png> <css1> [<css2> ...] — обвести найденные элементы и снять скриншот
                url, png = sys.argv[2], sys.argv[3]
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                colors = ["red", "blue", "green", "magenta", "orange"]
                for i, css in enumerate(sys.argv[4:]):
                    loc = page.locator(css)
                    vis = [loc.nth(k) for k in range(loc.count()) if loc.nth(k).is_visible()]
                    print(f"[{colors[i % 5]}] {css} -> всего {loc.count()}, видимых {len(vis)}")
                    for el in vis:
                        el.evaluate("(e, c) => { e.style.outline = '3px solid ' + c; e.style.outlineOffset = '2px'; }",
                                    colors[i % 5])
                page.screenshot(path=str(OUT / png), full_page=False)
            elif cmd == "phototest":
                page.goto(sys.argv[2], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                thumbs = page.locator("#hook_Block_UserPagePortletPhotos a.photo[href*='PopLayerPhoto']")
                print("thumbs:", thumbs.count())
                thumbs.first.click()
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT / "photo1.png"))
                print("layer:", page.locator(".photo-layer").count(), page.locator("#hook_Block_PhotoLayer").count())
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(2000)
                page.screenshot(path=str(OUT / "photo2.png"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "photo3.png"))
                print("after esc layer:", page.locator(".photo-layer").count(), "url:", page.url)
            elif cmd == "friendmenu":
                page.goto(sys.argv[2], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                cards = page.locator(".user-grid-card[data-id]")
                print("cards:", cards.count())
                card = cards.first
                card.hover()
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "fmenu1.png"))
                trig = card.locator(".user-grid-card_menu-button-trigger")
                print("trigger:", trig.count(), "visible:", trig.first.is_visible())
                trig.first.click()
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT / "fmenu2.png"))
                menu = page.locator("xpath=//*[contains(normalize-space(.),'Удалить из друзей') and string-length(normalize-space(.)) < 30]")
                print("remove items:", menu.count())
                for i in range(menu.count()):
                    el = menu.nth(i)
                    print("visible:", el.is_visible(), "|", el.evaluate("e => e.outerHTML")[:600])
                cont = menu.first.locator("xpath=ancestor::*[contains(@class,'sc-menu') or contains(@class,'menu')][1]")
                if cont.count():
                    print("----- menu container -----")
                    print(cont.first.evaluate("e => e.outerHTML")[:3000])
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
                page.screenshot(path=str(OUT / "fmenu3.png"))
            elif cmd == "friendmenu2":
                page.goto(sys.argv[2], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                card = page.locator(".user-grid-card[data-id]").first
                card.locator(".user-grid-card_img").first.hover()
                page.wait_for_timeout(2000)
                item = page.locator("xpath=//*[normalize-space(.)='Прекратить дружбу']")
                print("items:", item.count())
                for i in range(item.count()):
                    el = item.nth(i)
                    print("visible:", el.is_visible(), "tag:", el.evaluate("e => e.tagName"), "|",
                          el.evaluate("e => e.outerHTML")[:500])
                cont = item.first.locator(
                    "xpath=ancestor::*[contains(@class,'sc-menu') or contains(@class,'tip') or contains(@class,'popup')][1]")
                print("container count:", cont.count())
                if cont.count():
                    print(cont.first.evaluate("e => e.outerHTML")[:2500])
                tabs = page.locator("a.nav_menu_tab")
                for i in range(tabs.count()):
                    t = tabs.nth(i)
                    print("TAB", t.get_attribute("data-l"), "|", t.get_attribute("href"), "|",
                          t.inner_text().replace("\n", " "))
            elif cmd == "unfriend_dialog":
                page.goto(sys.argv[2], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                card = page.locator(".user-grid-card[data-id]").first
                print("card:", card.get_attribute("data-id"))
                card.hover()
                page.wait_for_timeout(1500)
                item = page.locator(".sc-menu:not(.sc-menu__hidden) a.u-menu_a[aria-label='Прекратить дружбу']")
                print("visible menu items:", item.count())
                page.screenshot(path=str(OUT / "unfriend1.png"))
                box = item.first.bounding_box()
                print("item box:", box)
                page.mouse.move(box["x"] + box["width"] * 0.4, box["y"] + box["height"] / 2, steps=15)
                page.wait_for_timeout(400)
                page.mouse.down()
                page.wait_for_timeout(80)
                page.mouse.up()
                page.wait_for_timeout(2500)
                page.screenshot(path=str(OUT / "unfriend2.png"))
                layer = page.locator("xpath=//*[contains(@id,'PopLayer') or contains(@class,'modal') or contains(@class,'layer')]"
                                     "[.//*[contains(normalize-space(.),'друж') or contains(normalize-space(.),'друз')]]")
                print("layers:", layer.count())
                for i in range(layer.count()):
                    el = layer.nth(i)
                    if el.is_visible():
                        html = el.evaluate("e => e.outerHTML")
                        if len(html) < 8000:
                            print(f"----- visible layer [{i}] -----")
                            print(html)
                            break
                btns = page.locator("button:visible, a.button-pro:visible, [role=button]:visible")
                for i in range(btns.count()):
                    b = btns.nth(i)
                    txt = b.inner_text().strip().replace("\n", " ")
                    if txt:
                        print("BTN", repr(txt[:40]), "|", b.evaluate("e => e.className")[:80])
                page.keyboard.press("Escape")
                page.wait_for_timeout(1500)
                page.screenshot(path=str(OUT / "unfriend3.png"))
                print("cards after:", page.locator(".user-grid-card[data-id]").count())
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

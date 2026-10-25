"""Режимы «Принять заявки» и «Удалить друзей» (быстрый режим, v0.2.0): клики подряд по видимым
карточкам, пауза 0,7–1,5 с, после порции — перезагрузка списка."""
import os
import random
import re
import time

from . import browser, store, paths
from .stop import Stopped, wait

# ---------- селекторы ОК (проверены 20.10.2026, см. REPORT.md) ----------
SEL = {
    # страница входящих заявок
    "req_block": "#hook_Block_UserFriendRequestMRB",
    "req_item": "#hook_Block_UserFriendRequestMRB .js-entity-item[data-entity-id]",
    "req_accept": "button.js-entity-accept:visible",
    "req_name": ".ucard-w_t a.o",
    "req_done_text": "Заявка принята",
    "req_error": ".entity-item_status.__error",
    "req_counter": "a[data-l='t,userFriendRequest'] .nav_menu_tab-counter",
    "req_empty_text": "Пока нет входящих заявок",
    # список друзей
    "fr_list": "[data-block='MyFriendsSquareCardsPagingB']",
    "fr_item": "[data-block='MyFriendsSquareCardsPagingB'] .user-grid-card[data-id]",
    "fr_avatar": ".user-grid-card_img",
    "fr_name": ".user-grid-card_name a",
    # меню друга — один общий блок на странице (#block_ShortcutMenu_FriendControlMenu_…), первая строка — имя;
    # если внизу мало места, открывается вверх (sc-menu__top)
    "fr_menu": ".sc-menu:not(.sc-menu__hidden)",
    "fr_menu_remove": ".sc-menu:not(.sc-menu__hidden) a.u-menu_a[aria-label='Прекратить дружбу']",
    "fr_menu_item": "a.u-menu_a[aria-label='Прекратить дружбу']",
    "fr_modal": "#hook_Modal_popLayerModal",
    "fr_modal_uid": "#hook_Modal_popLayerModal #field_userIds",
    "fr_confirm": "#hook_Modal_popLayerModal #hook_FormButton_button_delete_confirm",
    "fr_counter": "a[data-l='t,userFriend'] .nav_menu_tab-counter",
    "fr_empty_text": "Пока нет друзей",
    "fr_done_text": "удалён из списка ваших друзей",
    "fr_close_text": "Закрыть",
}

PAUSE = (0.7, 1.5)          # пауза между кликами, с
CONFIRM_S = 5.0             # сколько ждать ответа ОК на одно действие
MAX_UNCONFIRMED = 10        # столько неподтверждённых подряд — остановка
UNCONFIRMED_SHOTS = 5       # скриншотов неподтверждённых за запуск (кроме остановки)

INCIDENT_TEXTS = re.compile(
    r"введите код с картинки|подтвердите, что вы не робот|подтвердите номер|подтвердите телефон|"
    r"профиль заблокирован|слишком много действий|подозрительн|нарушени[ея] правил", re.I)
# окно ОК «у пользователя максимальное количество друзей» и похожие: у человека лимит — его пропускаем
PERSON_LIMIT = re.compile(
    r"максимальн\w*(?:\s+\S+){0,4}?\s+друз|друз\w*(?:\s+\S+){0,4}?\s+максимальн|"
    r"нельзя (?:добавить|принять)|не может (?:больше )?(?:добавить|иметь|принять)|"
    r"(?:лимит|предел)\w*\s+друз|у (?:пользователя|собеседника|него|неё|нее)(?:\s+\S+){0,3}?\s+(?:лимит|предел)", re.I)
# лимит у нас самих — дальше принимать бессмысленно
OWN_LIMIT = re.compile(
    r"у вас (?:уже )?(?:максимальн|слишком много друз)|вы достигли|вы не можете (?:больше )?(?:добавить|принять|иметь)|"
    r"ваш\w* (?:лимит|предел)", re.I)
DIALOG_WORDS = r"максимальн|друз|нельзя|лимит|предел|не может|не можете"

MSG_HELP = "Нужна ваша помощь — смотрите окно браузера. Когда разберётесь, нажмите «Сбросить и продолжить»"
MSG_CEILING = "Похоже, достигнут потолок друзей ОК. Можно приглашать в группу"
MSG_UNCONFIRMED = f"{MAX_UNCONFIRMED} действий подряд не подтвердились — остановлено, смотрите окно браузера"

# видимые окна поверх страницы: текст каждого
JS_DIALOGS = """() => {
  const sel = "[role=dialog],[role=alertdialog],[class*=modal],[id*=Modal],[class*=popLayer],[id*=popLayer],[class*=notify]";
  const out = [];
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(), st = getComputedStyle(e);
    if (r.width < 5 || r.height < 5 || st.visibility === 'hidden' || st.display === 'none') continue;
    const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();
    if (t) out.push(t.slice(0, 400));
  }
  return out;
}"""
# пометить крестик/кнопку закрытия в видимых окнах, текст которых подходит под шаблон
JS_TAG_CLOSE = """(src) => {
  const re = new RegExp(src, 'i');
  const sel = "[role=dialog],[role=alertdialog],[class*=modal],[id*=Modal],[class*=popLayer],[id*=popLayer],[class*=notify]";
  const close = "[aria-label*='акрыть'],[title*='акрыть'],[class*=close],[class*=Close],button";
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  let n = 0;
  for (const d of document.querySelectorAll(sel)) {
    if (!vis(d) || !re.test((d.innerText || '').replace(/\\s+/g, ' '))) continue;
    const btns = [...d.querySelectorAll(close)].filter(vis);
    const pick = btns.find(b => /закрыть|close|×|понятно|^ок$|^ok$/i.test((b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '') + ' ' + b.className + ' ' + (b.innerText || '').trim()))
              || btns.find(b => /close/i.test(b.className));
    if (pick) { pick.setAttribute('data-okf-close', '1'); n++; }
  }
  return n;
}"""
# состояние карточки заявки после «Принять»
JS_REQ_STATE = """([sel, uid, done]) => {
  const card = document.querySelector(sel + '[data-entity-id="' + uid + '"]');
  if (!card) return {s: 'gone'};
  const vis = e => !!(e && (e.offsetWidth || e.offsetHeight || e.getClientRects().length));
  const err = [...card.querySelectorAll('.entity-item_status.__error')].find(vis);
  if (err) return {s: 'error', t: (err.innerText || '').replace(/\\s+/g, ' ').trim()};
  const text = (card.innerText || '').replace(/\\s+/g, ' ').trim();
  if (text.includes(done)) return {s: 'done'};
  const btn = [...card.querySelectorAll('button.js-entity-accept')].find(vis);
  return {s: btn ? 'pending' : 'nobutton', t: text.slice(0, 200)};
}"""


def norm(text: str) -> str:
    """Пробелы как у людей: ОК ставит неразрывные («Пока нет\\xa0друзей»), переносы схлопываем."""
    return " ".join((text or "").replace("\xa0", " ").split())


class Incident(Exception):
    pass


class Finished(Exception):
    """Штатная остановка: кончились заявки/друзья, лимит за запуск, потолок, 10 неподтверждённых."""


class Session:
    def __init__(self, drv, page, mode: str, acc: dict):
        self.drv = drv
        self.page = page
        self.mode = mode
        self.acc = acc
        self.name = drv.account
        self.stop = drv.stop_event
        self.limit = store.run_limit(acc)
        self.done_run = 0
        self.unconfirmed = 0                # неподтверждённых подряд
        self.unconfirmed_shots = 0
        self.skipped = set()                # пропущены в этом запуске (не подтвердилось, лимит у человека)
        self.handled = set()                # уже принятые/удалённые в этом запуске
        self.not_found = 0
        self.urls = {}

    # ---------- служебное ----------
    def log(self, text: str) -> None:
        self.drv.log(self.mode, text)

    def status(self, text: str) -> None:
        self.drv.status(text)

    def today_done(self) -> int:
        return store.load_counters(self.name)[f"{self.mode}_today"]

    def body_text(self) -> str:
        try:
            return norm(self.page.locator("body").inner_text(timeout=5000))
        except Exception:
            return ""

    def screenshot(self, tag: str) -> str:
        stamp = store.now().strftime("%Y-%m-%d_%H-%M-%S")
        p = paths.SCREENSHOTS / f"{paths.safe_name(self.name)}_{stamp}_{tag}.png"
        try:
            self.page.screenshot(path=str(p))
        except Exception:
            pass
        return p.name

    def shot(self, tag: str) -> None:
        """Служебные скриншоты каждого шага (OKF_SHOTS=1, только из исходников или DEV_BUILD)."""
        if paths.test_hooks_enabled() and os.environ.get("OKF_SHOTS") == "1":
            self.log("шаг: скриншот " + self.screenshot(tag))

    def pause(self) -> None:
        wait(random.uniform(*PAUSE), self.stop)

    def incident(self, text: str) -> None:
        shot = self.screenshot("incident")
        store.record_incident(self.name, text)
        self.log(f"скриншот {shot}")
        self.status(MSG_HELP)
        raise Incident(text)

    def finish(self, text: str) -> None:
        store.set_last_stop(self.name, text)
        self.log(f"остановка: {text}; сделано за запуск {self.done_run}, за день {self.today_done()}")
        self.status(text)
        raise Finished(text)

    def goto(self, url: str) -> None:
        """Переход с одной повторной попыткой; предупреждение о сертификате не обходим (CertWarning).
        Ждём полной загрузки: на 24.10.2026 первое нажатие «Принять» сразу после domcontentloaded терялось —
        скрипты ОК ещё не подключены к кнопкам."""
        for attempt in range(2):
            try:
                browser.goto(self.page, url)
                try:
                    self.page.wait_for_load_state("load", timeout=15000)
                except Exception as e:
                    if browser.is_closed_error(e):
                        raise
                return
            except (browser.CertWarning, Stopped):
                raise
            except Exception as e:
                if browser.is_closed_error(e) or attempt:
                    raise
                self.log("страница не открылась, пробую ещё раз: " + " ".join(str(e).split())[:120])
                wait(2, self.stop)

    # ---------- проверки перед каждым действием ----------
    def guard(self) -> None:
        if self.stop.is_set():
            raise Stopped()
        store.rollover(self.name)
        if self.done_run >= self.limit:
            self.finish(f"Лимит за запуск выполнен: {self.done_run}")
        self.check_page()

    def check_page(self) -> None:
        """Капча, предупреждения, просьба подтвердить телефон, выход из сессии, предупреждение Chrome."""
        url = self.page.url
        if url.startswith("chrome-error://"):
            code = browser.cert_warning(self.page)
            if code:
                raise browser.CertWarning(code)
        if "anonym" in url or "st.cmd=anonymLogin" in url:
            self.finish("Сессия ОК потеряна. Войдите в ОК в окне браузера и нажмите кнопку ещё раз")
        if "captcha" in url.lower() or "verification" in url.lower():
            self.incident("страница проверки: " + url[:80])
        try:
            if self.page.locator("iframe[src*='captcha'], img[src*='captcha'], [id*='captcha' i]").count() > 0:
                self.incident("капча")
        except Exception:
            return
        m = INCIDENT_TEXTS.search(self.body_text())
        if m:
            self.incident("предупреждение ОК: «" + m.group(0) + "»")

    def unknown_page(self, what: str) -> None:
        shot = self.screenshot("unknown")
        self.log(f"не нашёл {what}; скриншот {shot}")
        self.finish(f"Не нашёл {what} — смотрите окно браузера")

    def hide_banners(self) -> None:
        """Рекламная плашка ОК внизу слева (#hook_Block_FixedPromoBanner, z-index 4000) перекрывает карточки
        и пункт меню друга, «Скрыть» её не убирает. Прячем у себя на странице, на сервер ничего не уходит.
        Зовётся перед каждым действием: после окна «удалён из списка» ОК перерисовывает страницу и плашку."""
        try:
            self.page.evaluate("() => document.querySelectorAll(\"#hook_Block_FixedPromoBanner, [class*='fixed_promo'],"
                               " [class*='fixed-promo']\").forEach(e => e.style.setProperty('display', 'none', 'important'))")
        except Exception:
            pass

    # ---------- окна ОК поверх страницы ----------
    def dialog_text(self) -> str:
        try:
            return " | ".join(self.page.evaluate(JS_DIALOGS))
        except Exception:
            return ""

    def close_dialogs(self) -> None:
        """Закрыть окно ОК: крестик/«Закрыть» внутри окна, затем Escape."""
        try:
            if self.page.evaluate(JS_TAG_CLOSE, DIALOG_WORDS):
                self.page.locator("[data-okf-close='1']").first.click(timeout=2000)
        except Exception:
            pass
        try:
            self.page.keyboard.press("Escape")
            self.page.evaluate("() => document.querySelectorAll('[data-okf-close]').forEach(e => e.removeAttribute('data-okf-close'))")
        except Exception:
            pass
        wait(0.3, self.stop)

    # ---------- итоги одного действия ----------
    def confirmed(self) -> None:
        self.unconfirmed = 0
        self.done_run += 1
        store.add_done(self.name, self.mode)
        self.drv.refresh()

    def not_confirmed(self, uid: str, what: str) -> None:
        """Не подтвердилось: в журнал, человека пропускаем, идём дальше; 10 подряд — остановка."""
        self.skipped.add(uid)
        self.unconfirmed += 1
        shot = ""
        if self.unconfirmed_shots < UNCONFIRMED_SHOTS:
            self.unconfirmed_shots += 1
            shot = "; скриншот " + self.screenshot("unconfirmed")
        self.log(f"не подтверждено ({self.unconfirmed} подряд), пропускаю: {what}{shot}")
        if self.unconfirmed >= MAX_UNCONFIRMED:
            self.log("скриншот " + self.screenshot("stop"))
            self.finish(MSG_UNCONFIRMED)

    def person_limit(self, uid: str, name: str) -> None:
        self.close_dialogs()
        self.limit_seen = len(PERSON_LIMIT.findall(self.body_text())) if self.mode == "accept" else 0
        self.skipped.add(uid)
        self.unconfirmed = 0
        self.log(f"пропуск: у человека максимум друзей — {name} ({uid})")

    # ---------- адреса ----------
    def resolve_urls(self) -> None:
        self.goto(browser.OK + "/friends")
        wait(1, self.stop)
        self.check_page()
        req = self.page.locator("a[href$='/friendRequests']").first
        fr = self.page.locator("a[href$='/friends']").first
        try:
            self.urls["requests"] = browser.OK + req.get_attribute("href", timeout=8000)
            self.urls["friends"] = browser.OK + fr.get_attribute("href", timeout=8000)
        except Exception:
            self.unknown_page("ссылки на заявки и друзей на странице друзей")
        self.log(f"страница заявок {self.urls['requests']}")

    def load_more(self, item_sel: str) -> bool:
        """Все загруженные карточки пройдены или пропущены — прокрутить вниз, вдруг ОК догрузит ещё."""
        before = self.page.locator(item_sel).count()
        for _ in range(3):
            self.page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            wait(1.2, self.stop)
            if self.page.locator(item_sel).count() > before:
                return True
        return False


# ======================= ПРИЁМ ЗАЯВОК =======================

class AcceptRun(Session):
    def open_list(self) -> bool:
        """Открыть (перезагрузить) заявки. False — заявок нет."""
        self.goto(self.urls["requests"])
        self.check_page()
        try:
            self.page.locator(SEL["req_block"]).wait_for(state="attached", timeout=4000)
        except Exception:
            pass
        if self.page.locator(SEL["req_block"]).count() == 0:
            if SEL["req_empty_text"] in self.body_text():
                return False
            self.not_found += 1
            if self.not_found >= 2:
                self.unknown_page("список заявок (незнакомая страница)")
            self.log("список заявок не нашёлся, пробую ещё раз")
            wait(2, self.stop)
            return self.open_list()
        self.not_found = 0
        self.hide_banners()
        # сколько раз фраза о максимуме друзей уже есть на странице: по тексту страницы ловим только новые
        self.limit_seen = len(PERSON_LIMIT.findall(self.body_text()))
        return True

    def counter(self) -> str:
        try:
            return norm(self.page.locator(SEL["req_counter"]).first.inner_text(timeout=1000))
        except Exception:
            return "?"

    def visible_uids(self) -> list:
        """Карточки с видимой кнопкой «Принять», сверху вниз, без обработанных и пропущенных."""
        items = self.page.locator(SEL["req_item"])
        out = []
        for i in range(items.count()):
            it = items.nth(i)
            uid = it.get_attribute("data-entity-id") or ""
            if not uid or uid in self.skipped or uid in self.handled:
                continue
            if it.locator(SEL["req_accept"]).count() == 0:
                continue
            out.append(uid)
        return out

    def accept_one(self, uid: str) -> None:
        self.hide_banners()
        card = self.page.locator(f"{SEL['req_item']}[data-entity-id='{uid}']").first
        name = ""
        try:
            name = norm(card.locator(SEL["req_name"]).first.inner_text(timeout=1000))
        except Exception:
            pass
        try:
            card.locator(SEL["req_accept"]).first.click(timeout=5000)
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            return self.not_confirmed(uid, f"кнопка «Принять» не нажалась для {name} ({uid})")
        # успех: на карточке «Заявка принята» или кнопка исчезла (или исчезла сама карточка)
        end = time.monotonic() + CONFIRM_S
        nobutton_since = None
        while time.monotonic() < end:
            wait(0.25, self.stop)
            dlg = self.dialog_text()
            if dlg:
                if OWN_LIMIT.search(dlg):
                    self.log(f"ОК: «{dlg[:150]}»; скриншот {self.screenshot('limit')}")
                    self.finish(MSG_CEILING)
                if PERSON_LIMIT.search(dlg):
                    return self.person_limit(uid, name)
            try:
                st = self.page.evaluate(JS_REQ_STATE, [SEL["req_item"], uid, SEL["req_done_text"]])
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
                continue
            if st["s"] in ("done", "gone"):
                self.confirmed()
                self.log(f"принята заявка: {name} ({uid}); заявок по счётчику {self.counter()}")
                return
            if st["s"] == "error":
                text = norm(st.get("t", ""))
                if OWN_LIMIT.search(text):
                    self.log(f"ОК: «{text}»; скриншот {self.screenshot('limit')}")
                    self.finish(MSG_CEILING)
                if PERSON_LIMIT.search(text):
                    return self.person_limit(uid, name)
                return self.not_confirmed(uid, f"ОК ответил ошибкой «{text}» для {name} ({uid})")
            if st["s"] == "nobutton":
                # кнопка пропала, а надписи нет: даём ОК секунду показать ошибку, иначе это успех
                nobutton_since = nobutton_since or time.monotonic()
                if time.monotonic() - nobutton_since >= 1.0:
                    self.confirmed()
                    self.log(f"принята заявка: {name} ({uid}) (кнопка исчезла); заявок по счётчику {self.counter()}")
                    return
        # ответа нет: может, ОК сказал о лимите не окном, а новой надписью где-то на странице
        text = self.body_text()
        if OWN_LIMIT.search(text):
            self.log(f"скриншот {self.screenshot('limit')}")
            self.finish(MSG_CEILING)
        if len(PERSON_LIMIT.findall(text)) > self.limit_seen:
            return self.person_limit(uid, name)
        self.close_dialogs()
        self.not_confirmed(uid, f"после «Принять» нет «{SEL['req_done_text']}» для {name} ({uid})")

    def run(self) -> None:
        self.log(f"старт приёма (быстрый режим): лимит за запуск {self.limit}, пауза "
                 f"{PAUSE[0]}–{PAUSE[1]} с, сегодня уже {self.today_done()}")
        self.status(f"Принимаю заявки (лимит за запуск {self.limit})")
        self.resolve_urls()
        while True:
            self.guard()
            if not self.open_list():
                self.finish("Заявки кончились")
            uids = self.visible_uids()
            if not uids and self.load_more(SEL["req_item"]):
                uids = self.visible_uids()
            if not uids:
                left = f": остались пропущенные ({len(self.skipped)})" if self.skipped else ""
                self.finish("Заявки кончились" + left)
            for uid in uids:
                self.guard()
                self.accept_one(uid)
                self.handled.add(uid)
                self.status(f"Принимаю заявки: за запуск {self.done_run} из {self.limit}, сегодня {self.today_done()}")
                self.pause()


# ======================= УДАЛЕНИЕ ДРУЗЕЙ =======================

class DeleteRun(Session):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.keep = store.read_do_not_delete()

    def open_list(self) -> bool:
        """Открыть (перезагрузить) список друзей. False — друзей нет."""
        self.goto(self.urls["friends"])
        self.check_page()
        try:
            self.page.locator(SEL["fr_list"]).wait_for(state="attached", timeout=4000)
        except Exception:
            pass
        if self.page.locator(SEL["fr_list"]).count() == 0:
            if SEL["fr_empty_text"] in self.body_text():
                return False
            self.not_found += 1
            if self.not_found >= 2:
                self.unknown_page("список друзей (незнакомая страница)")
            self.log("список друзей не нашёлся, пробую ещё раз")
            wait(2, self.stop)
            return self.open_list()
        self.not_found = 0
        self.hide_banners()
        return True

    def counter(self) -> str:
        try:
            return norm(self.page.locator(SEL["fr_counter"]).first.inner_text(timeout=1000))
        except Exception:
            return "?"

    def visible_uids(self) -> list:
        items = self.page.locator(SEL["fr_item"])
        out = []
        for i in range(items.count()):
            uid = items.nth(i).get_attribute("data-id") or ""
            if not uid or uid in self.handled or uid in self.skipped:
                continue
            if uid in self.keep or f"https://ok.ru/profile/{uid}" in self.keep:
                continue
            out.append(uid)
        return out

    def delete_one(self, uid: str) -> None:
        self.hide_banners()
        card = self.page.locator(f"{SEL['fr_item']}[data-id='{uid}']").first
        name = ""
        try:
            name = norm(card.locator(SEL["fr_name"]).first.inner_text(timeout=1000))
        except Exception:
            pass
        who = f"{name} ({uid})"
        # 1) навести на аватар — всплывает меню; не всплыло — отвести мышь и навести ещё раз.
        #    Меню ищем по имени человека: иначе можно взять ещё не спрятанное меню предыдущего друга
        avatar = card.locator(SEL["fr_avatar"]).first
        if name:
            # textContent меню: имя может слипаться со следующей строкой; «Друг 1» ≠ «Друг 10»
            head = re.compile(r"^\s*" + r"\s+".join(map(re.escape, name.split())) + r"(?![0-9a-zа-яё])")
            item = self.page.locator(SEL["fr_menu"]).filter(has_text=head).locator(SEL["fr_menu_item"]).first
        else:
            item = self.page.locator(SEL["fr_menu_remove"]).first
        shown = False
        for attempt in range(2):
            try:
                avatar.scroll_into_view_if_needed(timeout=3000)
                if attempt:
                    self.page.mouse.move(5, 5)
                # мышью в центр аватара, а не avatar.hover(): всплывшее меню перекрывает аватар,
                # и hover() ждал бы, пока под мышью снова окажется аватар
                ab = avatar.bounding_box()
                if not ab:
                    raise LookupError("аватар без координат")
                self.page.mouse.move(ab["x"] + ab["width"] / 2, ab["y"] + ab["height"] / 2, steps=4)
                item.wait_for(state="visible", timeout=3000)
                shown = True
                break
            except Stopped:
                raise
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
        if not shown:
            return self.not_confirmed(uid, f"меню «Прекратить дружбу» не появилось для {who}")
        self.shot(f"menu_{uid}")
        # 2) пункт меню — клик по координатам (меню прячется, если мышь уходит в сторону)
        box = item.bounding_box()
        if not box:
            return self.not_confirmed(uid, f"пункт «Прекратить дружбу» без координат для {who}")
        self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=4)
        self.page.mouse.down()
        self.page.mouse.up()
        # 3) окно подтверждения именно для этого человека → «Прекратить»
        try:
            self.page.locator(f"{SEL['fr_modal_uid']}[value='{uid}']").wait_for(state="attached", timeout=5000)
            self.page.locator(SEL["fr_confirm"]).first.wait_for(state="visible", timeout=5000)
            self.shot(f"confirm_{uid}")
            self.page.locator(SEL["fr_confirm"]).first.click(timeout=5000)
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            self.close_dialogs()
            return self.not_confirmed(uid, f"окно подтверждения не появилось или «Прекратить» не нажалось для {who}")
        # 4) успех — окно подтверждения закрылось; ОК на его месте показывает «Этот человек удалён из
        #    списка ваших друзей» с кнопкой «Закрыть» — его закрываем, иначе оно перекроет следующие карточки
        end = time.monotonic() + CONFIRM_S
        while time.monotonic() < end:
            wait(0.25, self.stop)
            try:
                if not self.page.locator(SEL["fr_confirm"]).first.is_visible():
                    self.confirmed()
                    done = SEL["fr_done_text"] in norm(self.dialog_text())
                    self.log(f"удалён из друзей: {who}{'' if done else ' (окно закрылось без надписи)'}; "
                             f"друзей по счётчику {self.counter()}")
                    self.shot(f"result_{uid}")
                    self.close_result()
                    self.shot(f"closed_{uid}")
                    return
            except Stopped:
                raise
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
        self.close_dialogs()
        self.not_confirmed(uid, f"окно подтверждения не закрылось после «Прекратить» для {who}")

    def close_result(self) -> None:
        """Закрыть окно «удалён из списка ваших друзей»: «Закрыть» → Escape → перезагрузка списка."""
        modal = self.page.locator(SEL["fr_modal"])
        for step in range(3):
            try:
                if modal.count() == 0 or not modal.first.is_visible():
                    return
                if step == 0:
                    modal.first.get_by_text(SEL["fr_close_text"], exact=True).first.click(timeout=2000)
                elif step == 1:
                    self.page.keyboard.press("Escape")
                else:
                    self.log("окно «удалён из списка» не закрылось, перезагружаю список")
                    self.need_reload = True
                    return
            except Stopped:
                raise
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
            wait(0.4, self.stop)

    def run(self) -> None:
        self.log(f"старт удаления (быстрый режим): лимит за запуск {self.limit}, пауза "
                 f"{PAUSE[0]}–{PAUSE[1]} с, сегодня уже {self.today_done()}, не трогать: {len(self.keep)} записей")
        self.status(f"Удаляю друзей (лимит за запуск {self.limit})")
        self.resolve_urls()
        while True:
            self.guard()
            if not self.open_list():
                self.finish("Друзья кончились")
            uids = self.visible_uids()
            if not uids and self.load_more(SEL["fr_item"]):
                uids = self.visible_uids()
            if not uids:
                total = self.page.locator(SEL["fr_item"]).count()
                self.finish(f"Список пройден: осталось {total}, их не трогаем "
                            f"(do_not_delete.txt или не подтвердилось)")
            self.need_reload = False
            for uid in uids:
                self.guard()
                self.delete_one(uid)
                self.handled.add(uid)
                self.status(f"Удаляю друзей: за запуск {self.done_run} из {self.limit}, сегодня {self.today_done()}")
                self.pause()
                if self.need_reload:
                    break


def run(drv, page, mode: str, acc: dict) -> None:
    cls = AcceptRun if mode == "accept" else DeleteRun
    s = cls(drv, page, mode, acc)
    try:
        s.run()
    except Finished:
        pass
    except Incident as e:
        drv.log(mode, f"аккаунт остановлен до «Сбросить и продолжить»: {e}")
    finally:
        drv.refresh()

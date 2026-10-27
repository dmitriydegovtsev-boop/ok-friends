"""Режимы «Принять заявки» и «Удалить друзей» (быстрый режим, v0.2.0–v0.2.1): клики подряд по видимым
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
RECOVER_AFTER = 3           # столько неподтверждённых подряд — закрыть окна и перезагрузить страницу
MAX_RECOVERIES = 3          # столько таких перезагрузок подряд без единого успеха — остановка
RELOAD_EVERY = (30, 50)     # профилактическая перезагрузка страницы через столько действий (случайно)
UNCONFIRMED_SHOTS = 5       # скриншотов неподтверждённых за запуск (кроме перезагрузок и остановки)
OWN_CAP = 5990              # столько своих друзей по счётчику — приём не начинаем (потолок ОК 6000)
RECOUNT_EVERY = 100         # перечитывать свой счётчик друзей не реже, чем через столько принятий
LIMIT_STREAK = 3            # столько окон «максимальное количество друзей» подряд — это наш потолок

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
MSG_CEILING = "Друзей уже около 6000 — пригласи в группу и запусти удаление"
MSG_STUCK = "Не получается продолжить — смотрите окно браузера"

# Всплывающие уведомления ОК (найдено 27.10.2026 на «Павел.»): #NotificationsGrowl → .growl.__active,
# крестик .growl_close (title «скрыть»), рядом ссылка «Дружить»/«Принять» — её не трогаем никогда.
# Прячем у себя на странице стилем, как рекламную плашку; на сервер ОК ничего не уходит.
HIDE_CSS = ("#hook_Block_FixedPromoBanner, [class*='fixed_promo'], [class*='fixed-promo'], #NotificationsGrowl, "
            "#hook_Block_NotificationsGrowl_Content, .growl, .toast, .fixed-toast, [class*='Toast_wrapper'], "
            "[class*='ToastAlert_wrapper'], .hobby_toast_manager_toast {display: none !important}")
# Перед каждым действием: закрыть видимые всплывашки крестиком, спрятать их, повесить стиль. Незнакомые
# всплывашки — по признакам: текст про дружбу/заявку, сидит в fixed-элементе, внутри есть крестик или «Принять».
JS_PREP = """(css) => {
  const out = {closed: [], hidden: document.visibilityState === 'hidden'};
  const vis = e => { const r = e.getBoundingClientRect(), st = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && st.display !== 'none' && st.visibility !== 'hidden'; };
  const txt = e => (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim();
  const KEEP = "[data-block='MyFriendsSquareCardsPagingB'], #hook_Block_UserFriendRequestMRB, #hook_Modal_popLayerModal, .sc-menu";
  const BAD = /принять|дружить|добавить|перейти|ответить|подписаться|открыть|прекратить/i;
  const isClose = b => /close|закрыть|скрыть/i.test((b.className && b.className.baseVal !== undefined ? b.className.baseVal : b.className || '') + ' ' +
      (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')) || /^[×✕✖x]$/i.test(txt(b));
  const fixedUp = e => { for (let p = e; p && p !== document.body; p = p.parentElement) if (getComputedStyle(p).position === 'fixed') return true; return false; };
  const roots = new Set();
  document.querySelectorAll(".growl, .toast, .fixed-toast, [class*='Toast_wrapper'], [class*='ToastAlert_wrapper'], .hobby_toast_manager_toast").forEach(e => roots.add(e));
  const xp = document.evaluate("//text()[contains(., 'ружит') or contains(., 'в друзья') or contains(., 'рекомендуем') or contains(., 'заявк')]",
                               document.body, null, XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
  for (let i = 0; i < xp.snapshotLength && i < 400; i++) {
    let e = xp.snapshotItem(i).parentElement;
    if (!e || e.closest(KEEP) || !fixedUp(e)) continue;
    // корень всплывашки — ближайший предок с крестиком или кнопкой «Принять», не шире 700 и не выше 400
    for (let p = e, n = 0; p && p !== document.body && n < 8; p = p.parentElement, n++) {
      const r = p.getBoundingClientRect();
      if (r.width > 700 || r.height > 400) break;
      if ([...p.querySelectorAll('*')].some(b => isClose(b) || /^принять$/i.test(txt(b)))) { roots.add(p); break; }
    }
  }
  for (const t of roots) {
    if (!t.isConnected || t.closest(KEEP) || !vis(t)) continue;
    const text = txt(t);
    if (!/ружит|в друзья|рекомендуем|заявк|друз/i.test(text) && !t.matches(".growl, .toast, .fixed-toast, [class*='Toast']")) continue;
    const x = [...t.querySelectorAll('*')].filter(vis).find(b => isClose(b) && !BAD.test(txt(b)));
    if (x) x.click();
    t.style.setProperty('display', 'none', 'important');
    out.closed.push((x ? '' : 'без крестика: ') + text.slice(0, 70));
  }
  if (!document.getElementById('okf-hide')) {
    const s = document.createElement('style'); s.id = 'okf-hide'; s.textContent = css;
    (document.head || document.documentElement).appendChild(s);
  }
  return out;
}"""
# Что лежит в точке: тег#id.классы «первые слова текста»
JS_DESCRIBE = """function okfDescribe(t) {
  if (!t) return 'ничего (точка вне окна)';
  const cls = (typeof t.className === 'string' ? t.className : (t.className && t.className.baseVal) || '').trim().split(/\\s+/).filter(Boolean).slice(0, 3);
  const words = (t.innerText || t.textContent || '').replace(/\\s+/g, ' ').trim().split(' ').slice(0, 6).join(' ');
  return t.tagName.toLowerCase() + (t.id ? '#' + t.id : '') + (cls.length ? '.' + cls.join('.') : '') + (words ? ' «' + words.slice(0, 60) + '»' : '');
}"""
# Цель в видимую зону: карточку в середину окна по вертикали, затем точка цели, которую ничто не перекрывает
JS_AIM = """([sel, shift]) => {
  """ + JS_DESCRIBE + """
  const el = [...document.querySelectorAll(sel)].find(e => e.offsetWidth || e.offsetHeight);
  if (!el) return {err: 'элемент не найден'};
  let r = el.getBoundingClientRect();
  const H = innerHeight;
  if (shift) window.scrollBy(0, shift);
  else if (r.top < H * 0.25 || r.bottom > H * 0.75) el.scrollIntoView({block: 'center', inline: 'nearest'});
  r = el.getBoundingClientRect();
  const pts = [[.5, .5], [.5, .7], [.5, .3], [.3, .5], [.7, .5], [.5, .85], [.5, .15], [.25, .8], [.75, .8]];
  for (const [fx, fy] of pts) {
    const x = r.left + r.width * fx, y = r.top + r.height * fy;
    if (x < 1 || y < 1 || x > innerWidth - 2 || y > H - 2) continue;
    const t = document.elementFromPoint(x, y);
    if (t && (t === el || el.contains(t))) return {x, y};
  }
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2, t = document.elementFromPoint(cx, cy);
  // поверх — меню предыдущего друга, которое ещё не спряталось: это лечится отводом мыши, а не прокруткой
  return {x: cx, y: cy, blocked: okfDescribe(t), menu: !!(t && t.closest('.sc-menu'))};
}"""
# что поверх точки (x, y) и попадает ли она в элемент sel
JS_OVER = """([x, y, sel]) => {
  """ + JS_DESCRIBE + """
  const t = document.elementFromPoint(x, y);
  const el = sel ? [...document.querySelectorAll(sel)].find(e => e.offsetWidth || e.offsetHeight) : null;
  if (t && el && (t === el || el.contains(t))) return 'сама цель (' + okfDescribe(t) + ')';
  return okfDescribe(t);
}"""

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


def parse_count(text: str) -> int | None:
    """Число со счётчика ОК: «5 873», «5\\xa0873», «6K», «5,9K», «5,9 тыс.» → 5873, 6000, 5900. Нет числа — None."""
    t = norm(text).lower().replace(" ", "").replace(" ", "")
    m = re.search(r"(\d+)(?:[.,](\d+))?(k|к|тыс)?", t)
    if not m:
        return None
    whole, frac, k = m.groups()
    if not k:
        # «1,234» без «K» — это разделитель тысяч, а не дробь
        return int(whole + (frac or "")) if frac and len(frac) == 3 else int(whole)
    return int(round(float(f"{whole}.{frac or 0}") * 1000))


# свой счётчик друзей: вкладка «Все» на странице друзей и заявок (24.10.2026 давал «9» при 9 друзьях)
JS_OWN_COUNT = """() => {
  const tabs = [...document.querySelectorAll("a[data-l='t,userFriend']")];
  const all = tabs.find(a => /^\\s*Все/.test(a.innerText || a.textContent || ''));
  const c = (all && all.querySelector('.nav_menu_tab-counter')) || document.querySelector("a[data-l='t,userFriend'] .nav_menu_tab-counter");
  return c ? (c.innerText || c.textContent || '') : null;
}"""
# карточки своих друзей: только из списка друзей; карточки с «Добавить в друзья»/«Дружить» (рекомендации) — никогда
JS_FR_UIDS = """(sel) => [...document.querySelectorAll(sel)]
  .filter(c => !/Добавить в друзья|Дружить/i.test(c.innerText || c.textContent || ''))
  .map(c => c.getAttribute('data-id'))"""
# окно ОК прямо про наш лимит или прямо про чужой
PERSON_EXPLICIT = re.compile(r"у (?:пользователя|собеседника|человека|него|неё|нее)\b", re.I)


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
        self.fails = {}                     # uid → сколько раз не подтвердилось
        self.streak = []                    # uid текущей серии неподтверждённых
        self.recoveries = 0                 # перезагрузок восстановления подряд без успеха
        self.reload_now = None              # причина перезагрузить страницу до следующего действия
        self.since_reload = 0
        self.reload_every = random.randint(*RELOAD_EVERY)
        self.last_point = None              # куда последний раз целились мышью/кнопкой
        self.last_target = None
        self.limit_streak = []              # тексты окон «максимум друзей» подряд (приём)
        self.limit_seen = 0
        self.own_count = "не виден"
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

    def prep(self) -> None:
        """Перед каждым действием. Рекламная плашка ОК внизу слева (#hook_Block_FixedPromoBanner, z-index 4000)
        и всплывашки ОК («… хочет с вами подружиться», «Новый рекомендуемый друг») перекрывают карточки и меню
        друга: видимые всплывашки закрываем крестиком, всё это прячем у себя на странице стилем, на сервер ничего
        не уходит. «Принять»/«Дружить» во всплывашке не нажимаются никогда. После окна «удалён из списка» ОК
        перерисовывает страницу и плашку — поэтому перед каждым действием. Свёрнутое окно Chrome — развернуть."""
        try:
            res = self.page.evaluate(JS_PREP, HIDE_CSS)
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            return
        for t in res.get("closed") or []:
            self.log(f"закрыто всплывающее уведомление ОК: «{t}»")
        if res.get("hidden"):
            self.restore_window()

    def restore_window(self) -> None:
        """Окно Chrome программы свёрнуто — разворачиваем: в свёрнутом окне меню ОК по наведению может не всплывать."""
        try:
            cdp = self.page.context.new_cdp_session(self.page)
            try:
                win = cdp.send("Browser.getWindowForTarget")
                cdp.send("Browser.setWindowBounds", {"windowId": win["windowId"], "bounds": {"windowState": "normal"}})
            finally:
                cdp.detach()
            self.log("окно Chrome программы было свёрнуто — развернул (не сворачивайте его во время работы)")
            wait(0.7, self.stop)
        except Stopped:
            raise
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            self.log("окно Chrome свёрнуто, развернуть не удалось: " + " ".join(str(e).split())[:120])

    def aim(self, sel: str) -> dict:
        """Цель (аватар друга, кнопка «Принять») — в середину окна по вертикали, не под шапкой и не под
        всплывашками; точка на ней, которую ничто не перекрывает (elementFromPoint). Перекрыта — закрыть
        всплывашки, прокрутить ещё и проверить снова. Возвращает {x, y} или {x, y, blocked: что поверх} / {err}."""
        res = {}
        shifts = [0, -160, 160, 0, 0]          # два последних — только после отвода мыши от меню
        for attempt in range(len(shifts)):
            shift = shifts[attempt]
            if attempt == 3 and not res.get("menu"):
                break
            if attempt:
                self.prep()
            try:
                res = self.page.evaluate(JS_AIM, [sel, shift])
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
                return {"err": " ".join(str(e).split())[:100]}
            if res.get("err"):
                return res
            self.last_point, self.last_target = (res["x"], res["y"]), sel
            if not res.get("blocked"):
                return res
            if res.get("menu"):
                # меню прошлого друга ещё открыто — увести мышь, ОК его спрячет; это не перекрытие,
                # следующая попытка без прокрутки
                self.page.mouse.move(5, 5)
                wait(0.6, self.stop)
                if attempt + 1 < len(shifts):
                    shifts[attempt + 1] = 0
                continue
            self.log(f"цель перекрыта: {res['blocked']} — прокручиваю и проверяю снова")
            wait(0.3, self.stop)
        return res

    def over(self) -> str:
        """Что сейчас лежит в точке, куда целились последний раз (для журнала при неподтверждённом действии)."""
        if not self.last_point:
            return "точка не выбрана"
        try:
            return self.page.evaluate(JS_OVER, [*self.last_point, self.last_target])
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            return "не удалось узнать"

    def own_friends(self) -> int | None:
        """Свой счётчик друзей с текущей страницы (вкладка «Все»); None — не виден."""
        try:
            raw = self.page.evaluate(JS_OWN_COUNT)
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            raw = None
        self.own_count = norm(raw) if raw and norm(raw) else "не виден"
        return parse_count(raw) if raw else None

    # ---------- окна ОК поверх страницы ----------
    def dialog_text(self) -> str:
        try:
            return " | ".join(self.page.evaluate(JS_DIALOGS))
        except Exception:
            return ""

    def close_dialogs(self, any_text: bool = False) -> None:
        """Закрыть окно ОК: крестик/«Закрыть» внутри окна, затем Escape. any_text — любые окна, не только про лимит."""
        try:
            if self.page.evaluate(JS_TAG_CLOSE, "." if any_text else DIALOG_WORDS):
                for h in self.page.locator("[data-okf-close='1']").element_handles()[:5]:
                    try:
                        h.click(timeout=2000)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self.page.keyboard.press("Escape")
            self.page.evaluate("() => document.querySelectorAll('[data-okf-close]').forEach(e => e.removeAttribute('data-okf-close'))")
        except Exception:
            pass
        wait(0.3, self.stop)

    # ---------- итоги одного действия ----------
    def confirmed(self, uid: str) -> None:
        self.unconfirmed = 0
        self.streak = []
        self.recoveries = 0
        self.limit_streak = []
        self.handled.add(uid)
        self.done_run += 1
        self.since_reload += 1
        store.add_done(self.name, self.mode)
        self.drv.refresh()
        if self.mode == "accept" and self.done_run % RECOUNT_EVERY == 0:
            self.reload_now = self.reload_now or "recount"
        elif self.since_reload >= self.reload_every:
            self.reload_now = self.reload_now or "profilactic"

    def not_confirmed(self, uid: str, what: str, over: str | None = None) -> None:
        """Не подтвердилось: в журнал (и что лежит поверх точки), человека пропускаем, идём дальше;
        3 подряд — закрыть окна и перезагрузить страницу (recover)."""
        over = over or self.over()
        self.skipped.add(uid)
        self.fails[uid] = self.fails.get(uid, 0) + 1
        self.streak.append(uid)
        self.unconfirmed += 1
        shot = ""
        if self.unconfirmed_shots < UNCONFIRMED_SHOTS:
            self.unconfirmed_shots += 1
            shot = "; скриншот " + self.screenshot("unconfirmed")
        self.log(f"не подтверждено ({self.unconfirmed} подряд), пропускаю: {what}; поверх точки: {over}{shot}")
        if self.unconfirmed >= RECOVER_AFTER:
            self.recover()

    def recover(self) -> None:
        """Самовосстановление (у друга 26.10: после нескольких десятков удалений меню ОК перестаёт всплывать,
        ручная перезагрузка помогает): скриншот, закрыть окна и всплывашки ОК, спрятать плашку, перезагрузить
        страницу. Люди из этой серии получают ещё одну попытку. 3 перезагрузки подряд без успеха — остановка."""
        if self.recoveries >= MAX_RECOVERIES:
            self.log(f"после {MAX_RECOVERIES} перезагрузок подряд ни одного успешного действия; "
                     f"скриншот {self.screenshot('stop')}")
            self.finish(MSG_STUCK)
        self.recoveries += 1
        self.log(f"{self.unconfirmed} действий подряд не подтвердились — закрываю окна ОК и перезагружаю страницу "
                 f"(перезагрузка {self.recoveries} из {MAX_RECOVERIES} без успеха); скриншот {self.screenshot('recover')}")
        self.close_dialogs(any_text=True)
        try:
            self.page.keyboard.press("Escape")
            self.page.mouse.move(5, 5)
        except Exception as e:
            if browser.is_closed_error(e):
                raise
        self.prep()
        for uid in self.streak:
            if self.fails.get(uid, 0) < 2:
                self.skipped.discard(uid)
        self.streak = []
        self.unconfirmed = 0
        self.reload_now = "recover"

    def note_reload(self) -> None:
        """В журнал — почему перезагружаем страницу (кроме восстановления: оно пишет само)."""
        why = {"profilactic": f"профилактическая перезагрузка страницы после {self.since_reload} действий",
               "recount": f"принято {self.done_run} — перезагружаю страницу и перечитываю свой счётчик друзей"}
        if self.reload_now in why:
            self.log(why[self.reload_now])

    def retry_left(self) -> bool:
        """Список кончился, а у кого-то не подтвердилось ровно один раз (например, последние в списке, когда
        страница «залипла» и до 3 подряд не дошло) — ещё одна попытка после перезагрузки. Второй раз — нет."""
        again = [u for u in self.skipped if self.fails.get(u, 0) == 1]
        if not again:
            return False
        for u in again:
            self.skipped.discard(u)
        self.log(f"список пройден, но у {len(again)} не подтвердилось один раз — перезагружаю страницу и пробую ещё раз")
        return True

    def after_reload(self) -> None:
        self.reload_now = None
        self.since_reload = 0
        self.reload_every = random.randint(*RELOAD_EVERY)

    def person_limit(self, uid: str, name: str, text: str) -> None:
        """Окно/надпись ОК «максимальное количество друзей». Прямо про нас — потолок. Иначе человек пропускается,
        но 3 таких подряд без единого принятия — тоже наш потолок (у друга 26.10 ОК так отвечал после 6000, а
        программа шла дальше). Если все три прямо про человека, а свой счётчик заметно ниже 6000 — идём дальше."""
        self.limit_streak.append(text)
        if len(self.limit_streak) >= LIMIT_STREAK:
            own = self.own_friends()
            if all(PERSON_EXPLICIT.search(t) for t in self.limit_streak) and own is not None and own < OWN_CAP - 100:
                self.log(f"{LIMIT_STREAK} окна «максимум друзей» подряд, но все про человека, а своих друзей "
                         f"по счётчику {self.own_count} — продолжаю")
                self.limit_streak = []
            else:
                self.log(f"{LIMIT_STREAK} окна «максимальное количество друзей» подряд — считаю, что это наш потолок; "
                         f"ОК: «{text[:150]}»; своих друзей по счётчику {self.own_count}; скриншот {self.screenshot('limit')}")
                self.finish(MSG_CEILING)
        self.close_dialogs()
        self.limit_seen = len(PERSON_LIMIT.findall(self.body_text())) if self.mode == "accept" else 0
        self.skipped.add(uid)
        self.unconfirmed = 0
        self.streak = []
        self.log(f"пропуск: у человека максимум друзей — {name} ({uid}); ОК: «{text[:120]}» "
                 f"({len(self.limit_streak)} подряд)")

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
        self.after_reload()
        self.prep()
        # сколько раз фраза о максимуме друзей уже есть на странице: по тексту страницы ловим только новые
        self.limit_seen = len(PERSON_LIMIT.findall(self.body_text()))
        return True

    def check_own_cap(self, say: bool) -> None:
        """Свой потолок: по счётчику «Все» уже 5990 и больше — не принимаем (ОК не даст больше 6000)."""
        n = self.own_friends()
        if say:
            self.log(f"своих друзей по счётчику: {self.own_count}" + ("" if n is not None else " (число не видно)"))
        if n is not None and n >= OWN_CAP:
            self.log(f"своих друзей {self.own_count} — это {n}, не меньше {OWN_CAP}; приём не начинаю")
            self.finish(MSG_CEILING)

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
        self.prep()
        card = self.page.locator(f"{SEL['req_item']}[data-entity-id='{uid}']").first
        name = ""
        try:
            name = norm(card.locator(SEL["req_name"]).first.inner_text(timeout=1000))
        except Exception:
            pass
        aim = self.aim(f"{SEL['req_item']}[data-entity-id='{uid}'] button.js-entity-accept")
        if aim.get("err") or aim.get("blocked"):
            return self.not_confirmed(uid, f"кнопка «Принять» {'перекрыта' if aim.get('blocked') else 'не нашлась'} "
                                           f"для {name} ({uid})", over=aim.get("blocked") or aim.get("err"))
        for attempt in range(2):
            try:
                card.locator(SEL["req_accept"]).first.click(timeout=4000)
                break
            except Stopped:
                raise
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
                # чаще всего клик перехватила всплывашка, выскочившая после прицеливания
                over = self.over()
                if attempt:
                    return self.not_confirmed(uid, f"кнопка «Принять» не нажалась для {name} ({uid})", over=over)
                self.log(f"«Принять» не нажалось ({over}) — закрываю всплывашки и жму ещё раз")
                self.prep()
                self.aim(f"{SEL['req_item']}[data-entity-id='{uid}'] button.js-entity-accept")
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
                    return self.person_limit(uid, name, norm(dlg))
            try:
                st = self.page.evaluate(JS_REQ_STATE, [SEL["req_item"], uid, SEL["req_done_text"]])
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
                continue
            if st["s"] in ("done", "gone"):
                self.confirmed(uid)
                self.log(f"принята заявка: {name} ({uid}); заявок по счётчику {self.counter()}")
                return
            if st["s"] == "error":
                text = norm(st.get("t", ""))
                if OWN_LIMIT.search(text):
                    self.log(f"ОК: «{text}»; скриншот {self.screenshot('limit')}")
                    self.finish(MSG_CEILING)
                if PERSON_LIMIT.search(text):
                    return self.person_limit(uid, name, text)
                return self.not_confirmed(uid, f"ОК ответил ошибкой «{text}» для {name} ({uid})")
            if st["s"] == "nobutton":
                # кнопка пропала, а надписи нет: даём ОК секунду показать ошибку, иначе это успех
                nobutton_since = nobutton_since or time.monotonic()
                if time.monotonic() - nobutton_since >= 1.0:
                    self.confirmed(uid)
                    self.log(f"принята заявка: {name} ({uid}) (кнопка исчезла); заявок по счётчику {self.counter()}")
                    return
        # ответа нет: может, ОК сказал о лимите не окном, а новой надписью где-то на странице
        text = self.body_text()
        if OWN_LIMIT.search(text):
            self.log(f"скриншот {self.screenshot('limit')}")
            self.finish(MSG_CEILING)
        found = PERSON_LIMIT.findall(text)
        if len(found) > self.limit_seen:
            return self.person_limit(uid, name, "надпись на странице: " + found[-1])
        over = self.over()
        self.close_dialogs()
        self.not_confirmed(uid, f"после «Принять» нет «{SEL['req_done_text']}» для {name} ({uid})", over=over)

    def run(self) -> None:
        self.log(f"старт приёма (быстрый режим): лимит за запуск {self.limit}, пауза "
                 f"{PAUSE[0]}–{PAUSE[1]} с, сегодня уже {self.today_done()}")
        self.status(f"Принимаю заявки (лимит за запуск {self.limit})")
        self.resolve_urls()
        first = True
        while True:
            self.guard()
            say = first or self.reload_now == "recount"
            self.note_reload()
            if not self.open_list():
                self.finish("Заявки кончились")
            # свой потолок: перед стартом и после каждой перезагрузки (в журнал — на старте и раз в 100 принятий)
            self.check_own_cap(say)
            first = False
            uids = self.visible_uids()
            if not uids and self.load_more(SEL["req_item"]):
                uids = self.visible_uids()
            if not uids and self.retry_left():
                continue
            if not uids:
                left = f": остались пропущенные ({len(self.skipped)})" if self.skipped else ""
                self.finish("Заявки кончились" + left)
            for uid in uids:
                self.guard()
                self.accept_one(uid)
                self.status(f"Принимаю заявки: за запуск {self.done_run} из {self.limit}, сегодня {self.today_done()}")
                self.pause()
                if self.reload_now:
                    break


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
        self.after_reload()
        self.prep()
        return True

    def counter(self) -> str:
        try:
            self.own_friends()
            return self.own_count if self.own_count != "не виден" else "?"
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            return "?"

    def visible_uids(self) -> list:
        """Только карточки из списка своих друзей (контейнер MyFriendsSquareCardsPagingB); блок «Возможно, вы
        знакомы» и карточки с «Добавить в друзья» не берутся никогда."""
        try:
            ids = self.page.evaluate(JS_FR_UIDS, SEL["fr_item"])
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            ids = []
        out = []
        for uid in ids:
            if not uid or uid in self.handled or uid in self.skipped:
                continue
            if uid in self.keep or f"https://ok.ru/profile/{uid}" in self.keep:
                continue
            out.append(uid)
        return out

    def delete_one(self, uid: str) -> None:
        self.prep()
        card = self.page.locator(f"{SEL['fr_item']}[data-id='{uid}']").first
        name = ""
        try:
            name = norm(card.locator(SEL["fr_name"]).first.inner_text(timeout=1000))
        except Exception:
            pass
        who = f"{name} ({uid})"
        # 1) навести на аватар — всплывает меню; не всплыло — отвести мышь и навести ещё раз.
        #    Меню ищем по имени человека: иначе можно взять ещё не спрятанное меню предыдущего друга
        avatar_sel = f"{SEL['fr_item']}[data-id='{uid}'] {SEL['fr_avatar']}"
        if name:
            # textContent меню: имя может слипаться со следующей строкой; «Друг 1» ≠ «Друг 10»
            head = re.compile(r"^\s*" + r"\s+".join(map(re.escape, name.split())) + r"(?![0-9a-zа-яё])")
            item = self.page.locator(SEL["fr_menu"]).filter(has_text=head).locator(SEL["fr_menu_item"]).first
        else:
            item = self.page.locator(SEL["fr_menu_remove"]).first
        shown = False
        blocked = None
        for attempt in range(2):
            try:
                if attempt:
                    self.page.mouse.move(5, 5)
                    wait(0.3, self.stop)
                    self.prep()
                # карточку в середину окна, точка на аватаре, которую ничто не перекрывает; мышью туда, а не
                # avatar.hover(): всплывшее меню перекрывает аватар, и hover() ждал бы, пока под мышью снова аватар
                aim = self.aim(avatar_sel)
                if aim.get("err"):
                    raise LookupError(aim["err"])
                if aim.get("blocked"):
                    blocked = aim["blocked"]
                    continue
                self.page.mouse.move(aim["x"], aim["y"], steps=4)
                item.wait_for(state="visible", timeout=3000)
                shown = True
                break
            except Stopped:
                raise
            except Exception as e:
                if browser.is_closed_error(e):
                    raise
        if not shown:
            return self.not_confirmed(uid, f"меню «Прекратить дружбу» не появилось для {who}", over=blocked)
        self.shot(f"menu_{uid}")
        # 2) пункт меню — клик по координатам (меню прячется, если мышь уходит в сторону); точку проверяем:
        #    поверх пункта не должно лежать ничего чужого
        try:
            box = item.bounding_box(timeout=2000)
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            box = None      # меню успело спрятаться
        if not box:
            return self.not_confirmed(uid, f"пункт «Прекратить дружбу» без координат для {who}")
        px, py = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.last_point, self.last_target = (px, py), None
        try:
            free = item.evaluate("(e, [x, y]) => { const t = document.elementFromPoint(x, y); return !!t && (t === e || e.contains(t)); }",
                                 [px, py])
        except Exception as e:
            if browser.is_closed_error(e):
                raise
            free = True
        if not free:
            return self.not_confirmed(uid, f"пункт «Прекратить дружбу» перекрыт для {who}")
        self.page.mouse.move(px, py, steps=4)
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
            over = self.over()
            self.close_dialogs()
            return self.not_confirmed(uid, f"окно подтверждения не появилось или «Прекратить» не нажалось для {who}",
                                      over=over)
        # 4) успех — окно подтверждения закрылось; ОК на его месте показывает «Этот человек удалён из
        #    списка ваших друзей» с кнопкой «Закрыть» — его закрываем, иначе оно перекроет следующие карточки
        end = time.monotonic() + CONFIRM_S
        while time.monotonic() < end:
            wait(0.25, self.stop)
            try:
                if not self.page.locator(SEL["fr_confirm"]).first.is_visible():
                    self.confirmed(uid)
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
        over = self.over()
        self.close_dialogs()
        self.not_confirmed(uid, f"окно подтверждения не закрылось после «Прекратить» для {who}", over=over)

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
                    self.reload_now = self.reload_now or "result"
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
            self.note_reload()
            if not self.open_list():
                self.finish("Друзья кончились")
            uids = self.visible_uids()
            if not uids and self.load_more(SEL["fr_item"]):
                uids = self.visible_uids()
            if not uids and self.retry_left():
                continue
            if not uids:
                total = len(self.page.evaluate(JS_FR_UIDS, SEL["fr_item"]))
                self.finish(f"Список пройден: осталось {total}, их не трогаем "
                            f"(do_not_delete.txt или не подтвердилось)")
            for uid in uids:
                self.guard()
                self.delete_one(uid)
                self.status(f"Удаляю друзей: за запуск {self.done_run} из {self.limit}, сегодня {self.today_done()}")
                self.pause()
                if self.reload_now:
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

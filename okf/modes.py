"""Режимы «Принять заявки» и «Удалить друзей»: селекторы ОК, темп, перерывы, отвлечения, инциденты."""
import os
import random
import re
import sys
import time
from datetime import timedelta

from . import browser, store, paths
from .human import Mouse, Stopped, wait, jitter

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
    # профиль человека
    "photo_thumbs": "#hook_Block_UserPagePortletPhotos a.photo[href*='PopLayerPhoto']",
    # список друзей
    "fr_list": "[data-block='MyFriendsSquareCardsPagingB']",
    "fr_item": "[data-block='MyFriendsSquareCardsPagingB'] .user-grid-card[data-id]",
    "fr_avatar": ".user-grid-card_img",
    "fr_name": ".user-grid-card_name a",
    "fr_menu_remove": ".sc-menu:not(.sc-menu__hidden) a.u-menu_a[aria-label='Прекратить дружбу']",
    "fr_modal": "#hook_Modal_popLayerModal",
    "fr_modal_uid": "#hook_Modal_popLayerModal #field_userIds",
    "fr_confirm": "#hook_Modal_popLayerModal #hook_FormButton_button_delete_confirm",
    "fr_counter": "a[data-l='t,userFriend'] .nav_menu_tab-counter",
    "fr_empty_text": "Пока нет друзей",
}

OK = "https://ok.ru"
INCIDENT_TEXTS = re.compile(
    r"введите код с картинки|подтвердите, что вы не робот|подтвердите номер|подтвердите телефон|"
    r"профиль заблокирован|слишком много действий|подозрительн|нарушени[ея] правил", re.I)
LIMIT_TEXTS = re.compile(r"лимит|максимальн\w+ (число|количество) друзей|больше друзей|не можете добавить", re.I)

MSG_HELP = "Нужна ваша помощь — смотрите окно браузера"
MSG_CEILING = "Похоже, достигнут потолок друзей ОК. Можно приглашать в группу"
MSG_UNCONFIRMED = "Действие не подтвердилось — остановлено, смотрите окно браузера"


class Incident(Exception):
    pass


class Finished(Exception):
    """Штатная остановка: заявки кончились, планка, окно, потолок."""


def _test_env(name: str, default: str = "") -> str:
    """Служебные переменные OKF_* работают только при запуске из исходников, в .exe — никогда."""
    if not paths.test_hooks_enabled():
        return default
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(_test_env(name, str(default)))
    except ValueError:
        return default


class Session:
    def __init__(self, drv, page, mode: str, acc: dict):
        self.drv = drv
        self.page = page
        self.mode = mode
        self.acc = acc
        self.name = drv.account
        self.stop = drv.stop_event
        self.mouse = Mouse(page, self.stop)
        self.cap = store.effective_cap(self.name, mode, acc)
        self.done_run = 0
        self.attempts = 0                   # попыток действия в этом запуске (для проверочного ограничителя)
        self.actions = 0                    # действий в этом запуске (для перерывов)
        self.next_short = random.randint(40, 80)
        self.next_long = random.randint(100, 150)
        self.skipped = set()
        self.handled = set()                # уже принятые/удалённые в этом запуске
        self.not_found = 0                  # элемент не нашёлся подряд
        self.fails = 0                      # «Принять» не сработало подряд
        self.urls = {}
        # служебные ограничители для проверок
        self.max_actions = _env_int("OKF_MAX_ACTIONS", 0)
        self.distract_first = _test_env("OKF_DISTRACT_FIRST") == "1"
        self.fake_fail = _test_env("OKF_FAIL_ACCEPT") == "1"
        self.base_pause = self._base_pause()

    # ---------- служебное ----------
    def log(self, text: str) -> None:
        self.drv.log(self.mode, text)

    def status(self, text: str) -> None:
        self.drv.status(text)

    def today_done(self) -> int:
        return store.load_counters(self.name)[f"{self.mode}_today"]

    def _base_pause(self) -> float:
        """Пауза между действиями, чтобы планка уложилась в рабочее окно (не меньше 2 с)."""
        window = store.window_minutes(self.acc) * 60
        cap = max(1, self.cap)
        est = (cap / 60) * 120                       # короткие перерывы
        est += (cap / 30) * 12                       # отвлечения: каждые 20–40 действий, 4–12 с плюс переходы
        if cap <= 1000:
            est += (cap / 125) * 1800                # длинные перерывы
        return max(2.0, (window - est) / cap)

    def pause(self, factor: float = 1.0) -> None:
        # OKF_PAUSE_SCALE — служебный множитель для проверок (в бою 1.0)
        try:
            scale = float(_test_env("OKF_PAUSE_SCALE", "1"))
        except ValueError:
            scale = 1.0
        wait(max(2.0, jitter(self.base_pause * factor * scale)), self.stop)

    def body_text(self) -> str:
        """Текст страницы с нормальными пробелами (ОК ставит неразрывные: «Пока нет\\xa0друзей»)."""
        try:
            return self.page.locator("body").inner_text(timeout=5000).replace("\xa0", " ")
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

    def incident(self, text: str) -> None:
        shot = self.screenshot("incident")
        store.record_incident(self.name, text)
        self.log(f"скриншот {shot}")
        self.status(MSG_HELP)
        raise Incident(text)

    def unconfirmed_stop(self, what: str) -> None:
        """Действие сделано, но результат не подтвердился: дальше не идём, аккаунт стоит."""
        shot = self.screenshot("unconfirmed")
        self.log(f"не подтверждено: {what}; скриншот {shot}")
        self.finish(MSG_UNCONFIRMED)

    def finish(self, text: str) -> None:
        store.set_last_stop(self.name, text)
        self.log(f"остановка: {text}; сделано за запуск {self.done_run}, за день {self.today_done()}, планка {self.cap}")
        self.status(text)
        raise Finished(text)

    # ---------- проверки перед каждым действием ----------
    def guard(self) -> None:
        if self.stop.is_set():
            raise Stopped()
        c = store.rollover(self.name, self.acc)
        if c.get("incident_day") == store.today_str():
            self.finish("Сегодня был инцидент — аккаунт стоит до конца дня")
        self.cap = store.effective_cap(self.name, self.mode, self.acc)
        if not store.in_work_window(self.acc):
            self.finish(f"Вне рабочего окна {self.acc['work_start']}–{self.acc['work_end']}")
        if self.today_done() >= self.cap:
            self.finish(f"Дневная планка {self.cap} достигнута")
        if self.max_actions and self.attempts >= self.max_actions:
            self.finish(f"Проверочный запуск: попыток {self.attempts}, сделано {self.done_run}")
        self.check_page()

    def check_page(self) -> None:
        """Капча, предупреждения, просьба подтвердить телефон, выход из сессии."""
        url = self.page.url
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

    # ---------- перерывы ----------
    def breaks(self) -> None:
        self.actions += 1
        if self.cap <= 1000 and self.actions >= self.next_long:
            self.next_long = self.actions + random.randint(100, 150)
            self._break(random.uniform(20 * 60, 40 * 60), "длинный перерыв")
        elif self.actions >= self.next_short:
            self.next_short = self.actions + random.randint(40, 80)
            self._break(random.uniform(60, 180), "короткий перерыв")

    def _break(self, seconds: float, label: str) -> None:
        until = (store.now() + timedelta(seconds=seconds)).strftime("%H:%M")
        self.log(f"{label} {int(seconds // 60)} мин, до {until}")
        self.status(f"{label.capitalize()} до {until}")
        wait(seconds, self.stop)

    # ---------- отвлечения ----------
    def want_distraction(self) -> bool:
        """Отвлечение через случайные 20–40 действий; интервал разыгрывается заново каждый раз."""
        if self.distract_first and self.actions == 0:
            self.distract_first = False
            return True
        if not hasattr(self, "next_distraction"):
            self.next_distraction = self.actions + random.randint(20, 40)
        if self.actions >= self.next_distraction:
            self.next_distraction = self.actions + random.randint(20, 40)
            return True
        return False

    def distract(self, uid: str) -> None:
        if random.random() < 0.08:
            self._visit_elsewhere()
            return
        self.log(f"отвлечение: смотрю профиль {uid}")
        self.status("Отвлечение: профиль человека")
        self.page.goto(f"{OK}/profile/{uid}", wait_until="domcontentloaded", timeout=45000)
        wait(random.uniform(1.5, 3.0), self.stop)
        self.check_page()
        dwell = random.uniform(4, 12)
        t0 = time.monotonic()
        self.mouse.scroll(random.randint(300, 1200))
        # вероятность полистать фото сама случайна: от 1 из 3 до 1 из 5, каждый раз заново
        if random.random() < 1.0 / random.uniform(3, 5):
            self._browse_photos()
        left = dwell - (time.monotonic() - t0)
        if left > 0:
            wait(left, self.stop)
        if random.random() < 0.5:
            self.mouse.scroll(-random.randint(200, 800))

    def _browse_photos(self) -> None:
        thumbs = self.page.locator(SEL["photo_thumbs"])
        if thumbs.count() == 0:
            return
        try:
            self.mouse.click(thumbs.nth(random.randrange(min(3, thumbs.count()))))
        except Exception:
            return
        n = random.randint(2, 5)
        self.log(f"листаю {n} фото")
        for _ in range(n):
            wait(random.uniform(1.2, 4.0), self.stop)
            self.page.keyboard.press("ArrowRight")
        wait(random.uniform(1.0, 3.0), self.stop)
        self.page.keyboard.press("Escape")
        wait(random.uniform(0.8, 1.5), self.stop)

    def _visit_elsewhere(self) -> None:
        where = random.choice([("/feed", "ленту"), ("/messages", "сообщения")])
        self.log(f"отвлечение: заглянул в {where[1]}")
        self.status(f"Отвлечение: {where[1]}")
        self.page.goto(OK + where[0], wait_until="domcontentloaded", timeout=45000)
        wait(random.uniform(2, 4), self.stop)
        self.check_page()
        for _ in range(random.randint(2, 5)):
            self.mouse.scroll(random.randint(200, 700))
            wait(random.uniform(2, 6), self.stop)
        wait(random.uniform(5, 12), self.stop)

    # ---------- адреса ----------
    def resolve_urls(self) -> None:
        self.page.goto(OK + "/friends", wait_until="domcontentloaded", timeout=45000)
        wait(2, self.stop)
        self.check_page()
        req = self.page.locator("a[href$='/friendRequests']").first
        fr = self.page.locator("a[href$='/friends']").first
        try:
            self.urls["requests"] = OK + req.get_attribute("href", timeout=8000)
            self.urls["friends"] = OK + fr.get_attribute("href", timeout=8000)
        except Exception:
            self.incident("не нашёл ссылки на заявки/друзей на странице друзей")
        self.log(f"страница заявок {self.urls['requests']}")


# ======================= ПРИЁМ ЗАЯВОК =======================

class AcceptRun(Session):
    def open_list(self) -> None:
        self.page.goto(self.urls["requests"], wait_until="domcontentloaded", timeout=45000)
        wait(random.uniform(1.5, 3.0), self.stop)
        self.check_page()
        if self.page.locator(SEL["req_block"]).count() == 0:
            if SEL["req_empty_text"] in self.body_text():
                self.finish("Заявки кончились")
            self.not_found += 1
            if self.not_found >= 2:
                self.incident("не нашёл список заявок (незнакомая страница)")
            self.log("список заявок не нашёлся, пробую ещё раз")
            wait(3, self.stop)
            return self.open_list()
        self.not_found = 0

    def counter(self) -> str:
        try:
            return self.page.locator(SEL["req_counter"]).first.inner_text(timeout=2000).strip()
        except Exception:
            return "?"

    def next_item(self):
        """Первая карточка с видимой кнопкой «Принять», не пропущенная и не обработанная."""
        items = self.page.locator(SEL["req_item"])
        for i in range(items.count()):
            it = items.nth(i)
            uid = it.get_attribute("data-entity-id") or ""
            if uid in self.skipped or uid in self.handled:
                continue
            if it.locator(SEL["req_accept"]).count() == 0:
                continue
            return it, uid
        return None, None

    def load_more(self) -> bool:
        before = self.page.locator(SEL["req_item"]).count()
        for _ in range(3):
            self.mouse.scroll(random.randint(600, 1400))
            wait(random.uniform(1.0, 2.5), self.stop)
            if self.page.locator(SEL["req_item"]).count() > before:
                return True
        # дошли до конца — перечитываем страницу: могли прийти новые
        self.open_list()
        item, _ = self.next_item()
        return item is not None

    def accept_one(self, item, uid: str) -> bool:
        name = ""
        try:
            name = item.locator(SEL["req_name"]).first.inner_text(timeout=3000).strip()
        except Exception:
            pass
        btn = item.locator(SEL["req_accept"]).first
        shots = _test_env("OKF_SHOTS") == "1"
        if shots:
            btn.scroll_into_view_if_needed()
            self.screenshot(f"before_{uid}")
        if self.fake_fail:
            self.mouse.hover(btn)
            wait(1, self.stop)
            self.log(f"проверка: «Принять» для {name} ({uid}) имитирую как несработавшее")
            return False
        if _test_env("OKF_FAKE_UNCONFIRMED") == "1":
            # проверка: навели, но не нажали — ответа ОК не будет, результат «не подтверждён»
            self.mouse.hover(btn)
            wait(1, self.stop)
            self.log(f"проверка: «Принять» для {name} ({uid}) без нажатия — имитирую неподтверждённое действие")
            self.ok_error = False
            return False
        self.mouse.click(btn)
        # ждём до 10 с: карточка перерисовывается — ищем её заново по id;
        # успех = кнопки «Принять» нет и видна надпись «Заявка принята» (или карточка исчезла)
        ok = False
        self.ok_error = False
        for _ in range(20):
            wait(0.5, self.stop)
            try:
                it = self.page.locator(f"{SEL['req_item']}[data-entity-id='{uid}']")
                if it.count() == 0:
                    ok = True
                    break
                it = it.first
                if it.locator(SEL["req_error"]).first.is_visible():
                    self.log(f"ОК ответил ошибкой принятия для {name} ({uid})")
                    self.ok_error = True
                    break
                if it.locator(SEL["req_accept"]).count() == 0 and \
                        it.get_by_text(SEL["req_done_text"]).first.is_visible():
                    ok = True
                    break
            except Exception:
                break
        if not ok:
            try:
                m = LIMIT_TEXTS.search(self.body_text())
                if m:
                    self.log(f"ОК сообщает о лимите: «{m.group(0)}»")
                    self.screenshot("limit")
                    self.finish(MSG_CEILING)
            except Finished:
                raise
            except Exception:
                pass
        else:
            self.log(f"принята заявка: {name} ({uid}); заявок по счётчику {self.counter()}")
        if shots:
            self.screenshot(f"after_{uid}")
        return ok

    def run(self) -> None:
        self.log(f"старт приёма: планка {self.cap}, окно {self.acc['work_start']}–{self.acc['work_end']}, "
                 f"базовая пауза {self.base_pause:.1f} с, сегодня уже {self.today_done()}")
        self.status(f"Принимаю заявки (планка {self.cap})")
        self.resolve_urls()
        self.open_list()
        while True:
            self.guard()
            item, uid = self.next_item()
            if item is None:
                if self.load_more():
                    continue
                self.finish("Заявки кончились")
            if random.random() < 1 / 15:
                self.skipped.add(uid)
                self.log(f"пропускаю {uid}")
                continue
            if self.want_distraction():
                self.distract(uid)
                self.open_list()
                continue
            self.attempts += 1
            ok = self.accept_one(item, uid)
            if ok:
                self.handled.add(uid)
                self.fails = 0
                self.done_run += 1
                store.add_done(self.name, "accept")
                self.drv.refresh()
                self.status(f"Принимаю заявки: сегодня {self.today_done()} из {self.cap}")
                self.breaks()
            elif self.ok_error or self.fake_fail:
                # ОК явно ответил ошибкой: это признак потолка друзей, трижды подряд — остановка
                self.fails += 1
                self.handled.add(uid)
                self.log(f"«Принять» не сработало, ОК ответил ошибкой ({self.fails} подряд)")
                if self.fails >= 3:
                    self.screenshot("ceiling")
                    self.finish(MSG_CEILING)
            else:
                # ни «Заявка принята», ни ошибки — результат неизвестен, дальше не идём
                self.unconfirmed_stop(f"принятие заявки {uid}")
            self.pause()


# ======================= УДАЛЕНИЕ ДРУЗЕЙ =======================

class DeleteRun(Session):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.keep = store.read_do_not_delete()
        self.miss = 0                       # элемент (меню, окно) не нашёлся подряд

    def open_list(self) -> None:
        self.page.goto(self.urls["friends"], wait_until="domcontentloaded", timeout=45000)
        wait(random.uniform(1.5, 3.0), self.stop)
        self.check_page()
        if self.page.locator(SEL["fr_list"]).count() == 0:
            if SEL["fr_empty_text"] in self.body_text():
                self.finish("Друзья кончились")
            self.not_found += 1
            if self.not_found >= 2:
                self.incident("не нашёл список друзей (незнакомая страница)")
            self.log("список друзей не нашёлся, пробую ещё раз")
            wait(3, self.stop)
            return self.open_list()
        self.not_found = 0

    def counter(self) -> str:
        try:
            return self.page.locator(SEL["fr_counter"]).first.inner_text(timeout=2000).strip()
        except Exception:
            return "?"

    def dismiss_banners(self) -> None:
        """Рекламные плашки ОК (например, «#МагнитныеИсторииОК») перекрывают карточки.
        Один раз за запуск пробуем кнопку «Скрыть»; дальше спасает сдвиг страницы (см. uncover)."""
        if getattr(self, "banner_tried", False):
            return
        self.banner_tried = True
        try:
            btns = self.page.get_by_role("button", name="Скрыть", exact=True)
            if btns.count() and btns.first.is_visible():
                self.log("закрываю плашку кнопкой «Скрыть»")
                self.mouse.click(btns.first)
                wait(random.uniform(0.6, 1.2), self.stop)
        except Stopped:
            raise
        except Exception:
            pass

    def uncover(self, target) -> None:
        """Если аватар перекрыт фиксированной плашкой, прокрутить страницу, чтобы карточка ушла из-под неё."""
        for _ in range(3):
            try:
                target.scroll_into_view_if_needed(timeout=5000)
                box = target.bounding_box()
                banners = self.page.locator("[class*='fixed_promo'], [class*='fixed-promo']")
                overlap = False
                for i in range(banners.count()):
                    b = banners.nth(i)
                    if not b.is_visible():
                        continue
                    bb = b.bounding_box()
                    if not box or not bb:
                        continue
                    if box["x"] < bb["x"] + bb["width"] and box["x"] + box["width"] > bb["x"] and \
                            box["y"] < bb["y"] + bb["height"] and box["y"] + box["height"] > bb["y"]:
                        overlap = True
                if not overlap:
                    return
                self.log("карточку перекрывает плашка, сдвигаю страницу")
                self.mouse.scroll(random.randint(250, 400))
                wait(random.uniform(0.5, 1.0), self.stop)
            except Stopped:
                raise
            except Exception:
                return

    def candidates(self) -> list:
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

    def pick(self) -> str | None:
        """Случайный человек из списка: иногда сначала прокручиваем, чтобы брать не только сверху."""
        for _ in range(random.randint(0, 3)):
            self.mouse.scroll(random.randint(500, 1500))
            wait(random.uniform(0.8, 2.0), self.stop)
        cands = self.candidates()
        if not cands:
            # дошли до конца видимого — попробуем догрузить
            before = self.page.locator(SEL["fr_item"]).count()
            for _ in range(3):
                self.mouse.scroll(random.randint(800, 1600))
                wait(random.uniform(1.0, 2.5), self.stop)
                if self.page.locator(SEL["fr_item"]).count() > before:
                    break
            cands = self.candidates()
        return random.choice(cands) if cands else None

    def delete_one(self, uid: str) -> bool:
        card = self.page.locator(f"{SEL['fr_item']}[data-id='{uid}']").first
        name = ""
        try:
            name = card.locator(SEL["fr_name"]).first.inner_text(timeout=3000).strip()
        except Exception:
            pass
        shots = _test_env("OKF_SHOTS") == "1"
        if shots:
            card.scroll_into_view_if_needed()
            self.screenshot(f"before_{uid}")
        # 1) навести на аватар — всплывает меню; если не всплыло, отвести мышь и навести ещё раз
        item = self.page.locator(SEL["fr_menu_remove"]).first
        shown = False
        for attempt in range(2):
            self.dismiss_banners()
            self.uncover(card.locator(SEL["fr_avatar"]).first)
            if attempt:
                # карточку могла закрыть плашка: сдвигаем страницу и наводим заново
                self.mouse.scroll(random.randint(200, 350))
                self.mouse.move_to(self.mouse.pos[0] + random.randint(-250, 250), self.mouse.pos[1] + random.randint(120, 300))
                wait(random.uniform(0.8, 1.5), self.stop)
            self.mouse.hover(card.locator(SEL["fr_avatar"]).first)
            wait(random.uniform(0.3, 0.8), self.stop)
            try:
                item.wait_for(state="visible", timeout=6000)
                shown = True
                break
            except Exception:
                continue
        if not shown:
            self.log(f"меню «Прекратить дружбу» не появилось для {name} ({uid}) после двух наведений")
            self.screenshot("no_menu")
            self.miss += 1
            if self.miss >= 2:
                self.incident("меню друга не нашлось дважды подряд")
            return False
        wait(random.uniform(0.4, 1.2), self.stop)
        # 2) пункт «Прекратить дружбу» — клик по координатам, меню прячется при уходе мыши
        box = item.bounding_box()
        if not box:
            self.miss += 1
            if self.miss >= 2:
                self.incident("пункт меню без координат дважды подряд")
            return False
        self.mouse.click_box(box)
        # 3) окно подтверждения именно для этого человека
        try:
            self.page.locator(f"{SEL['fr_modal_uid']}[value='{uid}']").wait_for(state="attached", timeout=8000)
            self.page.locator(SEL["fr_confirm"]).wait_for(state="visible", timeout=8000)
        except Exception:
            self.log(f"окно подтверждения не появилось для {name} ({uid})")
            self.screenshot("no_confirm")
            self.miss += 1
            if self.miss >= 2:
                self.incident("окно подтверждения не появилось дважды подряд")
            return False
        self.miss = 0
        wait(random.uniform(0.6, 1.8), self.stop)
        if shots:
            self.screenshot(f"confirm_{uid}")
        self.mouse.click(self.page.locator(SEL["fr_confirm"]).first)
        # 4) ждём, пока окно подтверждения закроется (до 10 с)
        for _ in range(20):
            wait(0.5, self.stop)
            try:
                if not self.page.locator(SEL["fr_confirm"]).first.is_visible():
                    break
            except Exception:
                break
        wait(random.uniform(1.0, 2.5), self.stop)
        if shots:
            self.screenshot(f"after_{uid}")
        # 5) проверка по перезагруженному списку: карточки человека больше нет
        self.open_list()
        ok = self.page.locator(f"{SEL['fr_item']}[data-id='{uid}']").count() == 0
        if shots:
            self.screenshot(f"reloaded_{uid}")
        if ok:
            self.log(f"удалён из друзей: {name} ({uid}); друзей по счётчику {self.counter()}")
            return True
        self.unconfirmed_stop(f"удаление {name} ({uid}) — человек всё ещё в списке")

    def run(self) -> None:
        self.log(f"старт удаления: планка {self.cap}, окно {self.acc['work_start']}–{self.acc['work_end']}, "
                 f"базовая пауза {self.base_pause:.1f} с, сегодня уже {self.today_done()}, "
                 f"не трогать: {len(self.keep)} записей")
        self.status(f"Удаляю друзей (планка {self.cap})")
        self.resolve_urls()
        self.open_list()
        self.failed = set()                 # меню/окно не открылось в этом запуске
        retried = False
        while True:
            self.guard()
            uid = self.pick()
            if uid is None:
                self.open_list()
                uid = self.pick()
            if uid is None and self.skipped:
                self.log(f"список пройден, возвращаюсь к пропущенным ({len(self.skipped)})")
                self.skipped.clear()
                continue
            if uid is None and self.failed and not retried:
                retried = True
                self.log(f"список пройден, повторная попытка для тех, у кого не открылось меню ({len(self.failed)})")
                self.handled -= self.failed
                self.failed.clear()
                continue
            if uid is None:
                total = self.page.locator(SEL["fr_item"]).count()
                if total == 0:
                    self.finish("Друзья кончились")
                self.finish(f"Список пройден: осталось {total}, их не трогаем (do_not_delete.txt или меню не открылось)")
            if random.random() < 1 / 15:
                self.skipped.add(uid)
                self.log(f"пропускаю {uid}")
                continue
            if self.want_distraction():
                self.distract(uid)
                self.open_list()
                continue
            self.attempts += 1
            ok = self.delete_one(uid)
            if ok:
                self.handled.add(uid)
                self.fails = 0
                self.done_run += 1
                store.add_done(self.name, "delete")
                self.drv.refresh()
                self.status(f"Удаляю друзей: сегодня {self.today_done()} из {self.cap}")
                self.breaks()
            else:
                # сюда попадаем только если меню/окно не нашлись (действия не было); второй промах — инцидент
                self.handled.add(uid)
                self.failed.add(uid)
                self.open_list()
            self.pause()


def run(drv, page, mode: str, acc: dict) -> None:
    cls = AcceptRun if mode == "accept" else DeleteRun
    s = cls(drv, page, mode, acc)
    try:
        s.run()
    except Finished:
        pass
    except Incident as e:
        drv.log(mode, f"аккаунт остановлен до конца дня: {e}")
    finally:
        drv.refresh()

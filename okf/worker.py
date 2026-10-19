"""Поток аккаунта: владеет Playwright и окном Chrome, выполняет команды «принять»/«удалить»."""
import queue
import threading
import traceback

from playwright.sync_api import sync_playwright

from . import browser, store, paths
from .human import Stopped

MSG_LOGIN = "Войдите в ОК в окне браузера и нажмите кнопку ещё раз"
MSG_CLOSED = "Окно браузера закрыто. Нажмите кнопку ещё раз, чтобы открыть заново"
MSG_HELP = "Нужна ваша помощь — смотрите окно браузера"
MSG_CEILING = "Похоже, достигнут потолок друзей ОК. Можно приглашать в группу"


class AccountDriver(threading.Thread):
    def __init__(self, name: str, app):
        super().__init__(daemon=True, name=f"drv-{name}")
        self.account = name
        self.app = app            # объект с методом post(account, kind, payload)
        self.cmd = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.ctx = None
        self.pw = None

    # ---- сообщения в окно ----
    def status(self, text: str) -> None:
        self.app.post(self.account, "status", text)

    def log(self, mode: str, text: str) -> None:
        line = store.append_log(self.account, mode, text)
        self.app.post(self.account, "log", line)

    def refresh(self) -> None:
        self.app.post(self.account, "counters", None)

    # ---- управление ----
    def request(self, mode: str) -> None:
        if self.busy:
            self.status("Уже выполняется. Сначала «Стоп»")
            return
        self.cmd.put(mode)

    def stop(self) -> None:
        self.stop_event.set()

    def quit(self) -> None:
        self.stop_event.set()
        self.cmd.put("quit")

    # ---- поток ----
    def run(self) -> None:
        with sync_playwright() as pw:
            self.pw = pw
            while True:
                cmd = self.cmd.get()
                if cmd == "quit":
                    self._close_ctx()
                    return
                self.stop_event.clear()
                self.busy = True
                try:
                    self._execute(cmd)
                except Stopped:
                    self.status("Остановлено")
                    self.log(cmd, "остановлено кнопкой «Стоп»")
                except browser.NoChrome:
                    self.app.post(self.account, "no_chrome", None)
                    self.status("Установите Google Chrome")
                except Exception as e:
                    if browser.is_closed_error(e):
                        self.ctx = None
                        self.status(MSG_CLOSED)
                        self.log(cmd, "окно браузера закрыто руками")
                    else:
                        self.log(cmd, "ошибка: " + " ".join(str(e).split())[:300])
                        self.status("Ошибка: " + " ".join(str(e).split())[:120])
                        traceback.print_exc()
                finally:
                    self.busy = False
                    self.refresh()

    def _close_ctx(self) -> None:
        if self.ctx is not None:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None

    def _launch(self):
        self.status("Открываю Chrome…")
        self.ctx = browser.launch_context(self.pw, self.account)
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))

    def _ensure_page(self):
        """Вернуть живую вкладку; если окно закрыли руками — закрыть остатки и открыть Chrome заново."""
        if self.ctx is None:
            self._launch()
        try:
            pages = self.ctx.pages
            if not pages:
                raise RuntimeError("browser has been closed: no pages")
            page = pages[0]
            for extra in pages[1:]:
                try:
                    extra.close()
                except Exception:
                    pass
            page.bring_to_front()
            return page
        except Exception as e:
            if not browser.is_closed_error(e):
                raise
            self.log("система", "окно браузера было закрыто, открываю заново")
            self._close_ctx()
            self._launch()
            pages = self.ctx.pages
            return pages[0] if pages else self.ctx.new_page()

    def _execute(self, mode: str) -> None:
        acc = store.account_settings(self.account)
        c = store.rollover(self.account, acc)
        today = store.today_str()
        if c.get("incident_day") == today:
            self.status("Сегодня был инцидент — аккаунт стоит до конца дня")
            self.log(mode, "запуск отклонён: сегодня был инцидент")
            return
        if not store.in_work_window(acc):
            self.status(f"Вне рабочего окна {acc['work_start']}–{acc['work_end']}")
            self.log(mode, "запуск отклонён: вне рабочего окна")
            return
        page = self._ensure_page()
        self.status("Проверяю вход в ОК…")
        if not browser.is_logged_in(page):
            self.status(MSG_LOGIN)
            self.log(mode, "нет сессии ОК, жду входа руками")
            return
        if mode == "check":
            self.status("Вход в ОК есть")
            self.log(mode, "вход в ОК подтверждён")
            return
        from . import modes
        modes.run(self, page, mode, acc)

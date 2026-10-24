"""Поток аккаунта: владеет Playwright и окном Chrome, выполняет команды «принять»/«удалить»."""
import queue
import threading
import traceback

from playwright.sync_api import sync_playwright

from . import browser, store, paths
from .stop import Stopped

MSG_LOGIN = "Войдите в ОК в окне браузера и нажмите кнопку ещё раз"
MSG_CLOSED = "Окно браузера закрыто. Нажмите кнопку ещё раз, чтобы открыть заново"
MSG_INCIDENT = "Был инцидент ({}). Посмотрите окно Chrome, затем нажмите «Сбросить и продолжить»"


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
                except browser.CertWarning as e:
                    self.cert_stop(cmd, e.code)
                except Exception as e:
                    if browser.is_closed_error(e):
                        # окно закрыли руками: закрываем остатки Chrome, иначе он без окон держит профиль
                        self._close_ctx()
                        self.status(MSG_CLOSED)
                        self.log(cmd, "окно браузера закрыто руками (" + " ".join(str(e).split())[:100] + ")")
                    else:
                        self.log(cmd, "ошибка: " + " ".join(str(e).split())[:300])
                        self.status("Ошибка: " + " ".join(str(e).split())[:120])
                        traceback.print_exc()
                finally:
                    self.busy = False
                    self.refresh()

    def screenshot(self, tag: str) -> str:
        stamp = store.now().strftime("%Y-%m-%d_%H-%M-%S")
        p = paths.SCREENSHOTS / f"{paths.safe_name(self.account)}_{stamp}_{tag}.png"
        try:
            if self.ctx is not None and self.ctx.pages:
                self.ctx.pages[0].screenshot(path=str(p))
        except Exception:
            pass
        return p.name

    def cert_stop(self, mode: str, code: str) -> None:
        """Предупреждение Chrome о сертификате: не обходим, останавливаемся со скриншотом."""
        shot = self.screenshot("cert")
        store.set_last_stop(self.account, browser.MSG_CERT)
        self.log(mode, f"остановка: предупреждение Chrome о сертификате, код {code or 'не виден'}; скриншот {shot}")
        self.status(browser.MSG_CERT)

    def _close_ctx(self) -> None:
        ctx, self.ctx = self.ctx, None
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        browser.kill_stale_chrome(self.account)

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
        store.ensure_counters(self.account)
        if mode == "reset":
            # «Сбросить и продолжить»: снять отметку инцидента и сразу запустить режим, который шёл
            store.clear_incident(self.account)
            mode = store.load_counters(self.account).get("last_mode") or "accept"
            self.log(mode, "«Сбросить и продолжить»: продолжаю " + ("приём" if mode == "accept" else "удаление"))
        c = store.rollover(self.account)
        if mode != "check" and c.get("incident_day"):
            self.status(MSG_INCIDENT.format(c.get("incident_text") or c["incident_day"]))
            self.log(mode, "запуск отклонён: отметка инцидента, нужна кнопка «Сбросить и продолжить»")
            return
        if mode in ("accept", "delete"):
            store.set_last_mode(self.account, mode)
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

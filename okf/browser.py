"""Запуск настоящего Google Chrome с персистентным профилем и проверка входа в ОК."""
import os
import sys
from pathlib import Path

from . import paths

CHROME_URL = "https://www.google.com/chrome"
OK_HOME = "https://ok.ru/"


class NoChrome(Exception):
    """Google Chrome не найден на компьютере."""


def find_chrome() -> str | None:
    cands = []
    if sys.platform == "win32":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if root:
                cands.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    elif sys.platform == "darwin":
        cands.append(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
        cands.append(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    else:
        for name in ("google-chrome", "google-chrome-stable"):
            for d in os.environ.get("PATH", "").split(os.pathsep):
                cands.append(Path(d) / name)
    for c in cands:
        if c.exists():
            return str(c)
    return None


# Playwright по умолчанию добавляет флаг --enable-automation, от которого Chrome
# выставляет navigator.webdriver = true и показывает плашку «браузером управляет
# автоматизированное ПО». Для обычной работы под аккаунтом человека это не нужно.
INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def launch_context(pw, account: str):
    """Открыть видимый Chrome с профилем profiles/<аккаунт>. Бросает NoChrome, если Chrome нет."""
    if not find_chrome():
        raise NoChrome()
    profile = paths.PROFILES / paths.safe_name(account)
    profile.mkdir(parents=True, exist_ok=True)
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chrome",
            headless=False,
            locale="ru-RU",
            viewport=None,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--window-size=1280,900",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
        )
    except Exception as e:  # playwright.sync_api.Error
        msg = str(e).lower()
        if "chrome" in msg and ("not found" in msg or "not installed" in msg or "failed to launch" in msg):
            raise NoChrome() from e
        raise
    ctx.add_init_script(INIT_SCRIPT)
    ctx.set_default_timeout(15000)
    return ctx


def is_closed_error(e: Exception) -> bool:
    msg = str(e).lower()
    return ("closed" in msg and ("target" in msg or "browser" in msg or "context" in msg or "page" in msg)) \
        or "connection closed" in msg


def is_logged_in(page) -> bool:
    """Открыть ok.ru и понять, есть ли сессия."""
    page.goto(OK_HOME, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    url = page.url
    if "anonymMain" in url or "/dk?st.cmd=anonym" in url:
        return False
    # поля формы входа — признак анонимной страницы
    for sel in ("#field_email", "input[name='st.email']", "form[action*='anonymLogin']"):
        try:
            if page.locator(sel).count() > 0:
                return False
        except Exception:
            pass
    # признак залогиненного пользователя: верхняя панель с меню пользователя
    for sel in ("#hook_Block_Navigation", ".toolbar_nav", "[data-l*='t,userMenu']", "#hook_Block_HeaderUserMenu",
                "a[href*='/profile/'][data-l*='userPage']", ".nav-side"):
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            pass
    return False

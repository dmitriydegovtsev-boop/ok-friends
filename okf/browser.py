"""Запуск настоящего Google Chrome с персистентным профилем и проверка входа в ОК."""
import os
import sys
import time
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


def kill_stale_chrome(account: str) -> int:
    """Добить процессы Chrome, запущенные с профилем этого аккаунта (окно закрыли, а процесс остался).
    Чужие Chrome не трогаем: ищем только по пути нашего профиля."""
    profile = str(paths.PROFILES / paths.safe_name(account))
    killed = 0
    try:
        import psutil
    except ImportError:
        return 0
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmd = proc.info.get("cmdline") or []
            if any(f"--user-data-dir={profile}" == a or a.startswith(f"--user-data-dir={profile}") for a in cmd):
                proc.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if killed:
        time.sleep(1.5)
    return killed


def launch_context(pw, account: str):
    """Открыть видимый Chrome с профилем profiles/<аккаунт>. Бросает NoChrome, если Chrome нет."""
    if not find_chrome():
        raise NoChrome()
    profile = paths.PROFILES / paths.safe_name(account)
    profile.mkdir(parents=True, exist_ok=True)
    kill_stale_chrome(account)
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
    if os.environ.get("OKF_INIT_SCRIPT", "0") == "1":
        ctx.add_init_script(INIT_SCRIPT)
    ctx.set_default_timeout(15000)
    # сразу открываем ok.ru, чтобы окно не висело на about:blank
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(OK_HOME, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
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

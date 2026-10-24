"""Настройки (settings.json), счётчики (counters/*.json), журналы (logs/*.txt), отметка инцидента."""
import json
import os
import threading
from datetime import datetime

from . import paths

_lock = threading.RLock()

RUN_LIMIT_MAX = 6000
SCHEMA = 2          # v0.2.0: отметки инцидентов прошлых версий при первом чтении снимаются

DEFAULT_ACCOUNT = {
    "run_limit": RUN_LIMIT_MAX,
}


# ---------- время (подменяется переменной OKF_FAKE_NOW для проверок) ----------

def now() -> datetime:
    fake = os.environ.get("OKF_FAKE_NOW") if paths.test_hooks_enabled() else None
    if fake:
        try:
            return datetime.strptime(fake, "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return datetime.now()


def today_str() -> str:
    return now().strftime("%Y-%m-%d")


# ---------- settings.json ----------

def load_settings() -> dict:
    with _lock:
        if paths.SETTINGS.exists():
            try:
                data = json.loads(paths.SETTINGS.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("accounts", {})
                    return data
            except (OSError, ValueError):
                pass
        return {"accounts": {}}


def save_settings(data: dict) -> None:
    with _lock:
        tmp = paths.SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(paths.SETTINGS)


def account_settings(name: str) -> dict:
    data = load_settings()
    acc = dict(DEFAULT_ACCOUNT)
    acc.update(data["accounts"].get(name, {}))
    return acc


def update_account(name: str, **fields) -> dict:
    with _lock:
        data = load_settings()
        acc = dict(DEFAULT_ACCOUNT)
        acc.update(data["accounts"].get(name, {}))
        acc.update(fields)
        data["accounts"][name] = acc
        save_settings(data)
        return acc


def remove_account(name: str) -> None:
    with _lock:
        data = load_settings()
        data["accounts"].pop(name, None)
        save_settings(data)


def account_names() -> list:
    return list(load_settings()["accounts"].keys())


# ---------- counters/<аккаунт>.json ----------

def _counter_path(name: str):
    return paths.COUNTERS / f"{paths.safe_name(name)}.json"


def _default_counters() -> dict:
    d = today_str()
    return {
        "first_run": d,
        "day": d,
        "accept_today": 0,
        "delete_today": 0,
        "accept_total": 0,
        "delete_total": 0,
        "incident_day": None,
        "incident_text": "",
        "last_stop": "",
        "last_mode": "accept",
        "schema": SCHEMA,
    }


def load_counters(name: str) -> dict:
    with _lock:
        p = _counter_path(name)
        c = _default_counters()
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            c.update(raw)
            if int(raw.get("schema", 1)) < SCHEMA:
                # счётчики прошлой версии: остаются как есть, только отметка инцидента не учитывается
                old = raw.get("incident_day")
                c["incident_day"] = None
                c["incident_text"] = ""
                c["schema"] = SCHEMA
                save_counters(name, c)
                if old:
                    append_log(name, "система", f"v0.2.0: отметка инцидента прошлой версии ({old}) снята")
        return c


def ensure_counters(name: str) -> None:
    """Создать файл счётчиков при первом запуске аккаунта (фиксирует дату первого запуска)."""
    with _lock:
        if not _counter_path(name).exists():
            save_counters(name, _default_counters())


def save_counters(name: str, c: dict) -> None:
    with _lock:
        p = _counter_path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def rollover(name: str) -> dict:
    """Смена дня: сброс счётчиков «сегодня». Возвращает счётчики."""
    with _lock:
        c = load_counters(name)
        today = today_str()
        if c["day"] != today:
            c["day"] = today
            c["accept_today"] = 0
            c["delete_today"] = 0
            save_counters(name, c)
            append_log(name, "система", "новый день")
        return c


def record_incident(name: str, text: str) -> dict:
    """Отметка инцидента: аккаунт не запускается, пока не нажата «Сбросить и продолжить»."""
    with _lock:
        c = rollover(name)
        c["incident_day"] = today_str()
        c["incident_text"] = text
        save_counters(name, c)
        append_log(name, "инцидент", f"{text}; аккаунт стоит до кнопки «Сбросить и продолжить»")
        return c


def clear_incident(name: str) -> None:
    with _lock:
        c = load_counters(name)
        if c.get("incident_day"):
            append_log(name, "система", f"отметка инцидента снята кнопкой ({c.get('incident_text', '')})")
        c["incident_day"] = None
        c["incident_text"] = ""
        save_counters(name, c)


def set_last_mode(name: str, mode: str) -> None:
    with _lock:
        c = load_counters(name)
        c["last_mode"] = mode
        save_counters(name, c)


def run_limit(acc: dict) -> int:
    try:
        n = int(acc.get("run_limit", RUN_LIMIT_MAX))
    except (TypeError, ValueError):
        n = RUN_LIMIT_MAX
    return max(1, min(RUN_LIMIT_MAX, n))


def add_done(name: str, mode: str, n: int = 1) -> dict:
    with _lock:
        c = rollover(name)
        c[f"{mode}_today"] += n
        c[f"{mode}_total"] += n
        save_counters(name, c)
        return c


def set_last_stop(name: str, text: str) -> None:
    with _lock:
        c = load_counters(name)
        c["last_stop"] = text
        save_counters(name, c)


# ---------- logs/<аккаунт>.txt ----------

MODE_RU = {"accept": "приём", "delete": "удаление", "reset": "сброс", "check": "вход", "система": "система", "инцидент": "инцидент"}


def append_log(name: str, mode: str, text: str) -> str:
    stamp = now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [{MODE_RU.get(mode, mode)}] {text}"
    with _lock:
        p = paths.LOGS / f"{paths.safe_name(name)}.txt"
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return line


def read_do_not_delete() -> set:
    out = set()
    try:
        for line in paths.DO_NOT_DELETE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.add(line.rstrip("/"))
            # запоминаем и числовой id профиля, если он есть в ссылке
            tail = line.rstrip("/").rsplit("/", 1)[-1]
            if tail:
                out.add(tail)
    except OSError:
        pass
    return out

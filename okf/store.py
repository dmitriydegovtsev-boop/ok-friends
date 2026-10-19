"""Настройки (settings.json), счётчики (counters/*.json), журналы (logs/*.txt), разгон."""
import json
import os
import threading
from datetime import datetime, date, timedelta

from . import paths

_lock = threading.RLock()

SLIDER_MIN = 250
SLIDER_MAX = 6000
SLIDER_STEP = 50
RAMP_START = 250
RAMP_FLOOR = 100
HIGH_PACE = 500

DEFAULT_ACCOUNT = {
    "accept_limit": 250,
    "delete_limit": 250,
    "ramp": True,
    "work_start": "9:00",
    "work_end": "23:00",
    "warned_high": False,
}


# ---------- время (подменяется переменной OKF_FAKE_NOW для проверок) ----------

def now() -> datetime:
    fake = os.environ.get("OKF_FAKE_NOW")
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


# ---------- рабочее окно ----------

def parse_hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def window_minutes(acc: dict) -> int:
    """Длина рабочего окна в минутах."""
    start = parse_hm(acc["work_start"])
    end = parse_hm(acc["work_end"])
    if end <= start:
        end += 24 * 60
    return end - start


def in_work_window(acc: dict, moment: datetime | None = None) -> bool:
    moment = moment or now()
    start = parse_hm(acc["work_start"])
    end = parse_hm(acc["work_end"])
    if end <= start:
        end += 24 * 60
    cur = moment.hour * 60 + moment.minute
    if cur < start:
        cur += 24 * 60
    return start <= cur < end


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
        "ramp_cap": RAMP_START,
        "ramp_reason": "старт: планка 250",
        "incident_day": None,
        "incident_text": "",
        "last_stop": "",
    }


def load_counters(name: str) -> dict:
    with _lock:
        p = _counter_path(name)
        c = _default_counters()
        if p.exists():
            try:
                c.update(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return c


def save_counters(name: str, c: dict) -> None:
    with _lock:
        p = _counter_path(name)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def round10(x: float) -> int:
    return int(round(x / 10.0)) * 10


def rollover(name: str, acc: dict | None = None) -> dict:
    """Смена дня: сброс дневных счётчиков и пересчёт планки разгона. Возвращает счётчики."""
    acc = acc or account_settings(name)
    with _lock:
        c = load_counters(name)
        today = today_str()
        if c["day"] != today:
            prev = c["day"]
            slider_max = max(int(acc["accept_limit"]), int(acc["delete_limit"]))
            worked = c["accept_today"] + c["delete_today"] > 0
            if c.get("incident_day") == prev:
                c["ramp_reason"] = f"{prev}: был инцидент, планка {c['ramp_cap']} сохранена на новый день"
            elif worked:
                new_cap = min(round10(c["ramp_cap"] * 1.5), slider_max)
                if new_cap > c["ramp_cap"]:
                    c["ramp_reason"] = f"{prev}: спокойный день, планка {c['ramp_cap']} → {new_cap}"
                else:
                    c["ramp_reason"] = f"{prev}: спокойный день, планка {c['ramp_cap']} уже на бегунке"
                c["ramp_cap"] = new_cap
            else:
                c["ramp_reason"] = f"{prev}: аккаунт не работал, планка {c['ramp_cap']} без изменений"
            c["day"] = today
            c["accept_today"] = 0
            c["delete_today"] = 0
            save_counters(name, c)
            append_log(name, "система", f"новый день, планка {c['ramp_cap']} ({c['ramp_reason']})")
        return c


def effective_cap(name: str, mode: str, acc: dict | None = None) -> int:
    """Дневная планка для режима: разгон (если включён) не выше бегунка; без разгона — бегунок."""
    acc = acc or account_settings(name)
    slider = int(acc["accept_limit"] if mode == "accept" else acc["delete_limit"])
    if not acc.get("ramp", True):
        return slider
    c = rollover(name, acc)
    return min(int(c["ramp_cap"]), slider)


def record_incident(name: str, text: str) -> dict:
    with _lock:
        c = rollover(name)
        new_cap = max(RAMP_FLOOR, round10(c["ramp_cap"] / 2))
        c["ramp_reason"] = f"{today_str()}: инцидент ({text}), планка {c['ramp_cap']} → {new_cap}"
        c["ramp_cap"] = new_cap
        c["incident_day"] = today_str()
        c["incident_text"] = text
        save_counters(name, c)
        append_log(name, "инцидент", f"{text}; планка → {new_cap}; аккаунт стоит до конца дня")
        return c


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

MODE_RU = {"accept": "приём", "delete": "удаление", "система": "система", "инцидент": "инцидент"}


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

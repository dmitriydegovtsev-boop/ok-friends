"""Пути программы: всё лежит рядом с .exe (или рядом с папкой okf при запуске из исходников)."""
import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE = base_dir()
PROFILES = BASE / "profiles"
LOGS = BASE / "logs"
COUNTERS = BASE / "counters"
SCREENSHOTS = BASE / "screenshots"
SETTINGS = BASE / "settings.json"
DO_NOT_DELETE = BASE / "do_not_delete.txt"

DO_NOT_DELETE_HEADER = (
    "# Кого не удалять из друзей: по одной ссылке на профиль в строке, например https://ok.ru/profile/123456789\n"
)


def ensure_dirs() -> None:
    for d in (PROFILES, LOGS, COUNTERS, SCREENSHOTS):
        d.mkdir(parents=True, exist_ok=True)
    if not DO_NOT_DELETE.exists():
        DO_NOT_DELETE.write_text(DO_NOT_DELETE_HEADER, encoding="utf-8")


def safe_name(account: str) -> str:
    """Имя файла латиницей/цифрами для аккаунта (кириллица — в транслит)."""
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z", "и": "i",
        "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
        "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
        "э": "e", "ю": "yu", "я": "ya",
    }
    out = []
    for ch in account.lower():
        if ch in table:
            out.append(table[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("_")
    s = "".join(out).strip("_")
    return s or "account"

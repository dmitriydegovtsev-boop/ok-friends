"""OK-Friends: приём заявок в друзья и удаление друзей в Одноклассниках. Точка входа."""
import os
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fix_streams() -> None:
    """В .exe без консоли sys.stdout/stderr пустые — Playwright и трассировки падают на них.
    Направляем вывод в logs/app.txt рядом с программой."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        logs = base / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        f = open(logs / "app.txt", "a", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


_fix_streams()

from okf.gui import main  # noqa: E402

if __name__ == "__main__":
    main()

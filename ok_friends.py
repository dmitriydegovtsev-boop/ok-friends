"""OK-Friends: приём заявок в друзья и удаление друзей в Одноклассниках. Точка входа."""
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from okf.gui import main  # noqa: E402

if __name__ == "__main__":
    main()

"""Проверка разгона и окна времени без браузера (шаг Д): подменяем время через OKF_FAKE_NOW.

Запуск: python tools/test_ramp.py — работает на служебном аккаунте «_тест_разгона», файлы потом удаляются.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okf import store, paths  # noqa: E402

ACC = "_тест_разгона"


def at(day: str, hm: str = "12:00"):
    os.environ["OKF_FAKE_NOW"] = f"{day} {hm}"


def cap(mode="accept"):
    return store.effective_cap(ACC, mode)


def check(label, got, want):
    mark = "OK " if got == want else "FAIL"
    print(f"{mark} {label}: {got} (ожидалось {want})")
    return got == want


def main():
    paths.ensure_dirs()
    for p in (paths.COUNTERS / f"{paths.safe_name(ACC)}.json", paths.LOGS / f"{paths.safe_name(ACC)}.txt"):
        if p.exists():
            p.unlink()
    store.remove_account(ACC)
    store.update_account(ACC, accept_limit=1000, delete_limit=500, ramp=True, work_start="9:00", work_end="23:00")
    ok = True

    at("2026-11-01")
    store.ensure_counters(ACC)
    ok &= check("день 1: стартовая планка", cap(), 250)
    store.add_done(ACC, "accept", 250)
    ok &= check("день 1: счётчик дня", store.load_counters(ACC)["accept_today"], 250)

    at("2026-11-02")
    ok &= check("день 2 после спокойного дня: 250×1,5 → 380", cap(), 380)
    ok &= check("день 2: счётчик дня сброшен в полночь", store.load_counters(ACC)["accept_today"], 0)
    store.add_done(ACC, "accept", 10)

    at("2026-11-03")
    ok &= check("день 3: 380×1,5=570 → 570", cap(), 570)
    store.add_done(ACC, "accept", 10)

    at("2026-11-04")
    ok &= check("день 4: 570×1,5=855 → 860 (до десятков)", cap(), 860)
    store.add_done(ACC, "accept", 10)

    at("2026-11-05")
    ok &= check("день 5: 860×1,5=1290, но бегунок приёма 1000", cap(), 1000)
    ok &= check("день 5: для удаления планка 1000, бегунок 500 → 500", cap("delete"), 500)
    ok &= check("день 5: планка в счётчике не выше максимального бегунка", store.load_counters(ACC)["ramp_cap"], 1000)

    # инцидент в день 5
    store.record_incident(ACC, "капча (проверка)")
    ok &= check("день 5 после инцидента: 1000/2 → 500", cap(), 500)
    ok &= check("день 5: отметка инцидента на сегодня", store.load_counters(ACC)["incident_day"], "2026-11-05")

    at("2026-11-06")
    ok &= check("день 6 после инцидента: планка не растёт, остаётся 500", cap(), 500)
    store.add_done(ACC, "accept", 1)

    at("2026-11-07")
    ok &= check("день 7 спокойный: 500×1,5 → 750", cap(), 750)

    # нижняя граница: много инцидентов подряд
    for i in range(6):
        store.record_incident(ACC, f"инцидент {i}")
    ok &= check("после серии инцидентов планка не ниже 100", cap(), 100)

    # день без работы — планка не растёт
    at("2026-11-08")
    ok &= check("день 8 (в день 7 не работали после инцидента): без роста", cap(), 100)
    at("2026-11-09")
    ok &= check("день 9 (в день 8 не работали): без роста", cap(), 100)

    # галочка снята — планка равна бегунку
    store.update_account(ACC, ramp=False)
    ok &= check("разгон выключен: планка приёма = бегунок 1000", cap(), 1000)
    ok &= check("разгон выключен: планка удаления = бегунок 500", cap("delete"), 500)
    store.update_account(ACC, ramp=True)

    # окно времени
    acc = store.account_settings(ACC)
    at("2026-11-09", "08:59"); ok &= check("8:59 вне окна 9–23", store.in_work_window(acc), False)
    at("2026-11-09", "09:00"); ok &= check("9:00 в окне", store.in_work_window(acc), True)
    at("2026-11-09", "22:59"); ok &= check("22:59 в окне", store.in_work_window(acc), True)
    at("2026-11-09", "23:00"); ok &= check("23:00 вне окна", store.in_work_window(acc), False)
    store.update_account(ACC, work_start="7:00", work_end="1:00")
    acc = store.account_settings(ACC)
    at("2026-11-09", "00:30"); ok &= check("0:30 в окне 7–1", store.in_work_window(acc), True)
    at("2026-11-09", "01:00"); ok &= check("1:00 вне окна 7–1", store.in_work_window(acc), False)
    at("2026-11-09", "06:59"); ok &= check("6:59 вне окна 7–1", store.in_work_window(acc), False)
    ok &= check("длина окна 7–1 = 18 ч", store.window_minutes(acc), 18 * 60)

    print("ИТОГ:", "все проверки прошли" if ok else "есть ошибки")
    print("--- журнал тестового аккаунта ---")
    print((paths.LOGS / f"{paths.safe_name(ACC)}.txt").read_text(encoding="utf-8"))
    # уборка
    store.remove_account(ACC)
    for p in (paths.COUNTERS / f"{paths.safe_name(ACC)}.json", paths.LOGS / f"{paths.safe_name(ACC)}.txt"):
        if p.exists():
            p.unlink()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

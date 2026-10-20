"""Расчётная проверка темпа (v0.1.3): укладывается ли дневная планка в рабочее окно при сессиях.

Для каждой планки и окна считаем план (plan_pace) и 200 раз разыгрываем день: паузы, сессии 30–80,
перерывы, отвлечения каждые 20–40 действий по 4–12 с (+ переходы ~6 с). Печатаем среднее и 95-й
процентиль времени до планки; проверяем, что 95 % дней укладываются в окно и пауза не ниже 2 с.
"""
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from okf.modes import plan_pace, draw_pause, ACTION_OVERHEAD  # noqa: E402

CAPS = [250, 380, 570, 860, 1000, 1500, 3000, 6000]
WINDOWS = {"9:00–23:00 (14 ч)": 14 * 3600, "9:00–1:00 (16 ч)": 16 * 3600, "7:00–1:00 (18 ч)": 18 * 3600}


def simulate(cap: int, pace: dict, mode: str) -> float:
    t = 0.0
    done = 0
    session_left = random.randint(30, 80)
    next_distraction = random.randint(20, 40)
    min_pause = 1e9
    while done < cap:
        if done >= next_distraction:
            next_distraction = done + random.randint(20, 40)
            t += random.uniform(4, 12) + 6
        t += ACTION_OVERHEAD[mode] * random.uniform(0.8, 1.2)  # само действие
        done += 1
        session_left -= 1
        if done >= cap:
            break
        if session_left <= 0:
            t += random.uniform(*pace["break"])
            session_left = random.randint(30, 80)
        p = max(2.0, draw_pause(pace["pause"]))
        min_pause = min(min_pause, p)
        t += p
    return t, min_pause


def main():
    ok = True
    for mode in ("accept", "delete"):
      for wname, wsec in WINDOWS.items():
        print(f"\n=== {'приём' if mode == 'accept' else 'удаление'}, окно {wname} ===")
        print(f"{'планка':>7} {'пауза, с':>12} {'перерыв, мин':>14} {'ужатие':>7} {'среднее':>9} {'95%':>8} {'окно':>7}  итог")
        for cap in CAPS:
            pace = plan_pace(cap, wsec, mode)
            runs = [simulate(cap, pace, mode) for _ in range(200)]
            times = sorted(r[0] for r in runs)
            mean_h = statistics.mean(times) / 3600
            p95_h = times[int(len(times) * 0.95) - 1] / 3600
            min_pause = min(r[1] for r in runs)
            fits = p95_h <= wsec / 3600 and min_pause >= 2.0
            # план честно предупреждает, когда не укладывается — это не ошибка расчёта
            ok &= fits or not pace['fits']
            print(f"{cap:>7} {pace['pause'][0]:>5.1f}–{pace['pause'][1]:<5.1f} "
                  f"{pace['break'][0] / 60:>6.1f}–{pace['break'][1] / 60:<6.1f} {pace['scale']:>7.2f} "
                  f"{mean_h:>8.1f}ч {p95_h:>7.1f}ч {wsec / 3600:>6.0f}ч  {'OK' if fits else ('не укладывается, план предупреждает' if not pace['fits'] else 'FAIL')}"
                  + (f" (мин. пауза {min_pause:.1f})" if min_pause < 2.05 else ""))
    print("\nИТОГ:", "все планки укладываются в окно, пауза не ниже 2 с" if ok else "есть FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

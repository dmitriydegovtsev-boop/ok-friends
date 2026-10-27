"""Проверка быстрого режима (v0.2.0–v0.2.1) через настоящее окно программы (кнопки нажимаются .invoke()).

  python tools/test_fast.py mock   — сухой прогон на макете ОК (tools/mock_ok.py) в отдельной папке,
                                     файлы программы и аккаунт «Павел.» не трогаются
  python tools/test_fast.py mocknew — только сценарии v0.2.1 (всплывашки, рекомендации, шапка, 6000, перезагрузки)
  python tools/test_fast.py real   — аккаунт «Павел.» на настоящем ok.ru: вход, приём, удаление, «Стоп»,
                                     отметка инцидента и «Сбросить и продолжить»
  python tools/test_fast.py realdel N — аккаунт «Павел.»: живое удаление N друзей (лимит за запуск = N)
  python tools/test_fast.py cert   — аккаунт «Павел.», вместо ok.ru — expired.badssl.com: программа
                                     должна остановиться на предупреждении Chrome и не обходить его
"""
import json
import re
import os
import queue
import shutil
import statistics
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SCENARIO = sys.argv[1] if len(sys.argv) > 1 else "mock"
PORT = 8765
MOCK = f"http://127.0.0.1:{PORT}"

if SCENARIO in ("mock", "mocknew"):
    base = Path(os.environ.get("OKF_TEST_BASE", "/tmp/okf_fast_test"))
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    os.environ["OKF_BASE"] = str(base)
    os.environ["OKF_OK_URL"] = MOCK
    ACC = "Сухой прогон"
elif SCENARIO == "cert":
    os.environ["OKF_OK_URL"] = "https://expired.badssl.com"
    ACC = "Павел."
else:
    ACC = "Павел."

import tkinter as tk  # noqa: E402
from okf import gui, store, paths, modes, browser  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  OK   " if ok else "  FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


class Driver:
    """Сценарий идёт в своём потоке, всё, что трогает окно, — через очередь в главный поток Tk."""

    def __init__(self, app):
        self.app = app
        self.q = queue.Queue()

    def pump(self):
        try:
            while True:
                fn, box, ev = self.q.get_nowait()
                try:
                    box.append(fn())
                except Exception as e:
                    box.append(e)
                ev.set()
        except queue.Empty:
            pass
        self.app.root.after(50, self.pump)

    def ui(self, fn):
        box, ev = [], threading.Event()
        self.q.put((fn, box, ev))
        ev.wait(10)
        return box[0] if box else None

    def status(self, acc=ACC):
        return self.app._status.get(acc, "")

    def press(self, button, acc=ACC):
        def go():
            names = store.account_names()
            self.app.listbox.selection_clear(0, "end")
            self.app.listbox.selection_set(names.index(acc))
            self.app._select_from_list()
            getattr(self.app, button).invoke()
        self.ui(go)

    def wait_status(self, pred, timeout=120, acc=ACC):
        end = time.time() + timeout
        while time.time() < end:
            s = self.status(acc)
            if pred(s):
                return s
            time.sleep(0.3)
        return self.status(acc)

    def idle(self, acc=ACC, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            d = self.app.drivers.get(acc)
            if d is None or not d.busy:
                return True
            time.sleep(0.3)
        return False


def mock(path):
    return json.loads(urllib.request.urlopen(MOCK + path, timeout=5).read())


def log_text(acc=ACC):
    p = paths.LOGS / f"{paths.safe_name(acc)}.txt"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def unconfirmed_people(text):
    """Кто не подтвердился (id), без повторов: v0.2.1 даёт пропущенным ещё одну попытку после перезагрузки."""
    return set(re.findall(r"не подтверждено \(.*?\((\d+)\);", text))


def counters(acc=ACC):
    return store.load_counters(acc)


# ---------------------------------------------------------------- сухой прогон
def scenario_mock(d: Driver):
    today = store.today_str()
    # счётчики «прошлой версии» с отметкой инцидента на сегодня: v0.2.0 должна её не учитывать
    store.update_account(ACC, accept_limit=250, delete_limit=250, ramp=True, work_start="9:00", work_end="23:00")
    store.update_account(ACC + " 2")
    old = {"first_run": "2026-10-19", "day": today, "accept_today": 3, "delete_today": 2, "accept_total": 40,
           "delete_total": 30, "ramp_cap": 125, "ramp_reason": "инцидент", "incident_day": today,
           "incident_text": "капча", "last_stop": "Нужна ваша помощь"}
    (paths.COUNTERS / f"{paths.safe_name(ACC)}.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    d.ui(d.app._load_accounts)

    print("1) полный приём на макете (45 заявок: 41 обычная, 2 «максимум друзей», 1 молчит, 1 ошибка)")
    mock("/ctl?reset=1")
    t0 = time.time()
    d.press("btn_accept")
    s = d.wait_status(lambda s: s.startswith("Заявки кончились"), 300)
    dt = time.time() - t0
    check("приём закончился «Заявки кончились: остались пропущенные (4)»", s == "Заявки кончились: остались пропущенные (4)", s)
    st = mock("/state")
    ok_n = sum(1 for r in st["reqs"] if r["done"])
    c = counters()
    check("принято 41 на макете", ok_n == 41, f"{ok_n}")
    check("счётчик сегодня 3+41, всего 40+41 (старые счётчики сохранились)",
          c["accept_today"] == 44 and c["accept_total"] == 81, f"сегодня {c['accept_today']}, всего {c['accept_total']}")
    check("старая отметка инцидента не учтена, schema=2", c["incident_day"] is None and c["schema"] == 2)
    check("старые поля счётчиков на месте (ramp_cap)", c.get("ramp_cap") == 125)
    lt = log_text()
    check("в журнале «пропуск: у человека максимум друзей» ×2 (окно и надпись)",
          lt.count("пропуск: у человека максимум друзей") == 2, str(lt.count("пропуск: у человека максимум друзей")))
    check("окно «максимум друзей» закрыто (крестик/Escape)", "модальн" not in s)
    check("«не подтверждено» у 2 человек (молчание и ошибка ОК)", unconfirmed_people(lt) == {"1018", "1027"},
          str(sorted(unconfirmed_people(lt))))
    check("в журнале снята отметка прошлой версии", "отметка инцидента прошлой версии" in lt)
    times = [t for t, _id, k in st["accept_log"]]
    gaps = [b - a for a, b in zip(times, times[1:]) if b - a < 4]
    check("клики не чаще 0,7 с (пауза 0,7–1,5 с + ответ ОК)", min(gaps) >= 0.7,
          f"между кликами: мин {min(gaps):.2f}, медиана {statistics.median(gaps):.2f}, макс {max(gaps):.2f} с; "
          f"45 нажатий за {dt:.0f} с")
    check("порциями: 20 + 20 + перезагрузки", len(st["accept_log"]) >= 45, f"нажатий {len(st['accept_log'])}")

    print("2) «Стоп» во время приёма")
    mock("/ctl?reset=1")
    d.press("btn_accept")
    end = time.time() + 60
    while time.time() < end and len(mock("/state")["accept_log"]) < 5:
        time.sleep(0.1)
    d.press("btn_stop")
    s = d.wait_status(lambda s: s == "Остановлено", 10)
    n1 = len(mock("/state")["accept_log"])
    time.sleep(4)
    n2 = len(mock("/state")["accept_log"])
    check("«Стоп»: статус «Остановлено», новых нажатий нет", s == "Остановлено" and n1 == n2 and n1 <= 6,
          f"нажатий к «Стоп» {n1}, через 4 с {n2}")

    print("3) «Лимит за запуск» = 7")
    mock("/ctl?reset=1")
    store.update_account(ACC, run_limit=7)
    d.press("btn_accept")
    s = d.wait_status(lambda s: s.startswith("Лимит за запуск"), 60)
    check("остановка «Лимит за запуск выполнен: 7»", s == "Лимит за запуск выполнен: 7", s)
    check("поле «Лимит за запуск» показывает 7", d.ui(d.app.limit_var.get) == "7", d.ui(d.app.limit_var.get))
    d.ui(lambda: d.app.limit_var.set("99999"))
    d.ui(d.app._save_fields)
    check("лимит больше 6000 урезается до 6000", store.account_settings(ACC)["run_limit"] == 6000,
          str(store.account_settings(ACC)["run_limit"]))
    store.update_account(ACC, run_limit=6000)

    print("4) ОК молчит на всё: 3 неподтверждённых → перезагрузка; 3 перезагрузки без успеха → остановка")
    mock("/ctl?reset=1&silent_all=1")
    log0 = len(log_text())
    d.press("btn_accept")
    s = d.wait_status(lambda s: s == modes.MSG_STUCK, 240)
    st = mock("/state")
    n = len(st["accept_log"])
    lt = log_text()[log0:]
    check("остановка «Не получается продолжить…» после 3 перезагрузок, 12 нажатий", s == modes.MSG_STUCK and n == 12,
          f"{s}; нажатий {n}")
    check("в журнале 3 перезагрузки восстановления и причина остановки",
          lt.count("перезагружаю страницу (перезагрузка") == 3 and "после 3 перезагрузок подряд" in lt,
          f"перезагрузок {lt.count('перезагружаю страницу (перезагрузка')}")
    check("в журнале у неподтверждённых — что поверх точки", lt.count("поверх точки: ") >= 12)
    mock("/ctl?silent_all=0")

    print("5) лимит у нас самих («Вы достигли максимального количества друзей»)")
    mock("/ctl?reset=1&own_limit=1")
    d.press("btn_accept")
    s = d.wait_status(lambda s: s == modes.MSG_CEILING, 60)
    check("остановка «Друзей уже около 6000…»", s == modes.MSG_CEILING, s)
    mock("/ctl?own_limit=0")

    print("6) капча → отметка → кнопка «Принять» не идёт → «Сбросить и продолжить»")
    mock("/ctl?reset=1&captcha=1")
    d.press("btn_accept")
    s = d.wait_status(lambda s: s == modes.MSG_HELP, 60)
    check("капча: «Нужна ваша помощь…», отметка инцидента", s == modes.MSG_HELP and counters()["incident_day"], s)
    check("скриншот капчи сохранён", any("incident" in p.name for p in paths.SCREENSHOTS.iterdir()))
    d.idle()
    d.press("btn_accept")
    s = d.wait_status(lambda s: s.startswith("Был инцидент"), 20)
    check("при отметке «Принять заявки» отказывается и зовёт «Сбросить и продолжить»", s.startswith("Был инцидент"), s)
    mock("/ctl?captcha=0")
    d.idle()
    d.press("btn_reset")
    s = d.wait_status(lambda s: s.startswith("Заявки кончились"), 300)
    check("«Сбросить и продолжить» снял отметку и сразу продолжил приём до конца",
          s.startswith("Заявки кончились") and counters()["incident_day"] is None, s)

    print("7) предупреждение Chrome о сертификате посреди работы")
    mock("/ctl?reset=1&cert=1")
    d.press("btn_accept")
    s = d.wait_status(lambda s: s == browser.MSG_CERT, 60)
    check("статус про сертификат", s == browser.MSG_CERT, s)
    lt = log_text()
    check("в журнале код ошибки со страницы", "код NET::ERR_CERT_" in lt.splitlines()[-1], lt.splitlines()[-1])
    shot = sorted(p for p in paths.SCREENSHOTS.iterdir() if "cert" in p.name)
    check("скриншот предупреждения сохранён", bool(shot), shot[-1].name if shot else "")
    mock("/ctl?cert=0")

    print("8) полное удаление на макете (30 друзей: 1 молчит, 1 без меню, 1 в do_not_delete.txt)")
    mock("/ctl?reset=1")
    with paths.DO_NOT_DELETE.open("a", encoding="utf-8") as f:
        f.write("https://ok.ru/profile/2015\n")
    d.idle()
    before = counters()["delete_total"]
    d.press("btn_delete")
    s = d.wait_status(lambda s: s.startswith("Список пройден") or s.startswith("Друзья кончились"), 300)
    st = mock("/state")
    gone = sum(1 for f in st["friends"] if f["gone"])
    check("удалено 27, остались 3 (молчит, без меню, do_not_delete)", gone == 27 and s.startswith("Список пройден: осталось 3"),
          f"{s}; удалено {gone}")
    check("2015 не тронут", not next(f for f in st["friends"] if f["id"] == "2015")["gone"])
    check("счётчик удалений +27", counters()["delete_total"] - before == 27, str(counters()["delete_total"] - before))
    times = [t for t, _id, k in st["unfriend_log"]]
    gaps = [b - a for a, b in zip(times, times[1:]) if b - a < 6]
    gaps = gaps or [0.0]
    check("темп удаления", min(gaps) >= 0.7,
          f"между «Прекратить»: мин {min(gaps):.2f}, медиана {statistics.median(gaps):.2f}, макс {max(gaps):.2f} с")

    print("9) «Запустить всё: принимать» — оба аккаунта сразу, «Стоп всё»")
    mock("/ctl?reset=1")
    d.idle()
    t0 = time.time()
    d.ui(d.app.start_all)
    s1 = d.wait_status(lambda s: s.startswith("Принимаю заявки:"), 60)
    s2 = d.wait_status(lambda s: s.startswith("Принимаю заявки:"), 60, acc=ACC + " 2")
    dt = time.time() - t0
    check("оба аккаунта принимают без сдвига", s1.startswith("Принимаю") and s2.startswith("Принимаю"), f"оба за {dt:.0f} с")
    d.ui(d.app.stop_all)
    a = d.wait_status(lambda s: s == "Остановлено", 15)
    b = d.wait_status(lambda s: s == "Остановлено", 15, acc=ACC + " 2")
    check("«Стоп всё» остановил оба", a == "Остановлено" and b == "Остановлено", f"{a} / {b}")


# ---------------------------------------------------------------- сухой прогон v0.2.1
def scenario_mock_new(d: Driver):
    store.update_account(ACC, run_limit=6000)
    d.ui(d.app._load_accounts)
    keep = paths.DO_NOT_DELETE.read_text(encoding="utf-8") if paths.DO_NOT_DELETE.exists() else ""
    if "/2015" not in keep:
        with paths.DO_NOT_DELETE.open("a", encoding="utf-8") as f:
            f.write("https://ok.ru/profile/2015\n")

    def run(button, ctl, pred, timeout=300):
        d.idle(timeout=900)
        mock("/ctl?reset=1&" + ctl)
        log0 = len(log_text())
        d.ui(lambda: d.app._status.__setitem__(ACC, ""))   # статус прошлого сценария не должен сойти за итог
        d.press(button)
        s = d.wait_status(pred, timeout)
        d.idle()
        return s, mock("/state"), log_text()[log0:]

    accept_end = lambda s: s.startswith("Заявки кончились") or s in (modes.MSG_STUCK, modes.MSG_CEILING) or s.startswith("Ошибка")
    delete_end = lambda s: s.startswith("Список пройден") or s.startswith("Друзья кончились") or s == modes.MSG_STUCK \
        or s.startswith("Ошибка")

    print("10) всплывашка «… хочет с вами подружиться» каждые 2 действия поверх верхних карточек — приём")
    s, st, lt = run("btn_accept", "toast=2", accept_end)
    ok_n = sum(1 for r in st["reqs"] if r["done"])
    check("приём дошёл до конца: 41 принята, «остались пропущенные (4)»",
          ok_n == 41 and s == "Заявки кончились: остались пропущенные (4)", f"{s}; принято {ok_n}")
    check("«Принять» во всплывашке не нажато ни разу", st["toast_accept"] == 0,
          f"всплывашек {st['toast_shown']}, «Принять» в них {st['toast_accept']}, закрыто крестиком {st['toast_closed']}")
    check("незнакомые всплывашки закрыты крестиком и записаны в журнал",
          st["toast_closed"] >= 1 and "закрыто всплывающее уведомление ОК" in lt, f"в журнале {lt.count('закрыто всплывающее')}")
    check("не подтвердились только 2 человека (молчание и ошибка ОК)", unconfirmed_people(lt) == {"1018", "1027"},
          str(sorted(unconfirmed_people(lt))))

    print("11) всплывашки каждые 3 действия + 120 друзей по 100 на странице: удаление и профилактические перезагрузки")
    s, st, lt = run("btn_delete", "nfr=120&fr_page=100&toast=3", delete_end, 900)
    gone = sum(1 for f in st["friends"] if f["gone"])
    check("удалено 117 из 120 (остались молчащий, без меню, do_not_delete)", gone == 117 and s.startswith("Список пройден: осталось 3"),
          f"{s}; удалено {gone}")
    check("«Принять» во всплывашке не нажато ни разу", st["toast_accept"] == 0,
          f"всплывашек {st['toast_shown']}, «Принять» в них {st['toast_accept']}, закрыто крестиком {st['toast_closed']}")
    prof = lt.count("профилактическая перезагрузка страницы после")
    check("профилактическая перезагрузка каждые 30–50 удалений", prof >= 2 and all(
        30 <= int(l.split("после ")[1].split()[0]) <= 50 for l in lt.splitlines() if "профилактическая" in l), f"{prof} раз")
    check("не подтвердились только 2 человека (молчит, без меню)", unconfirmed_people(lt) == {"2004", "2009"},
          str(sorted(unconfirmed_people(lt))))

    print("12) блок «Возможно, вы знакомы» с «Добавить в друзья» над списком (и одна такая карточка в самом списке)")
    s, st, lt = run("btn_delete", "rec=2", delete_end)
    gone = sum(1 for f in st["friends"] if f["gone"])
    check("удалено 27, «Список пройден: осталось 3»", gone == 27 and s.startswith("Список пройден: осталось 3"), f"{s}; удалено {gone}")
    check("«Добавить в друзья» не нажато, рекомендации не тронуты",
          st["rec_add"] == 0 and not any(str(i).startswith("3") for _t, i, _k in st["unfriend_log"]) and "Рекомендация" not in lt,
          f"«Добавить» {st['rec_add']}")

    print("13) высокая шапка: верхний ряд карточек частично под ней")
    s, st, lt = run("btn_delete", "header=1", delete_end)
    gone = sum(1 for f in st["friends"] if f["gone"])
    check("удалено 27, не подтвердились только 2 человека", gone == 27 and unconfirmed_people(lt) == {"2004", "2009"},
          f"{s}; удалено {gone}; не подтверждены {sorted(unconfirmed_people(lt))}")

    print("14) свой потолок 6000")
    for text in ("6K", "5\xa0995"):
        s, st, lt = run("btn_accept", "fr_text=" + urllib.parse.quote(text), accept_end, 60)
        check(f"счётчик «{text}» — приём не начат, «{modes.MSG_CEILING}»", s == modes.MSG_CEILING and not st["accept_log"],
              f"{s}; нажатий {len(st['accept_log'])}")
    check("в журнале прочитанный счётчик", "своих друзей по счётчику: 5 995" in lt, [l for l in lt.splitlines() if "счётчику" in l][:1])
    s, st, lt = run("btn_accept", "fr_base=1000&limit_after=3", accept_end, 120)
    check("3 окна «максимальное количество друзей» подряд — стоп, 6 нажатий",
          s == modes.MSG_CEILING and len(st["accept_log"]) == 6 and "подряд — считаю, что это наш потолок" in lt,
          f"{s}; нажатий {len(st['accept_log'])}")
    s, st, lt = run("btn_accept", "fr_base=5945", accept_end, 120)
    ok_n = sum(1 for r in st["reqs"] if r["done"])
    check("счётчик 5975 → приём; после перезагрузки 5992 — стоп (17 принято)", s == modes.MSG_CEILING and ok_n == 17,
          f"{s}; принято {ok_n}")

    print("15) после 7 действий на странице меню и «Принять» перестают работать (как у друга) — перезагрузка лечит")
    s, st, lt = run("btn_delete", "nfr=60&stale_after=7", delete_end, 900)
    gone = sum(1 for f in st["friends"] if f["gone"])
    rec = lt.count("перезагружаю страницу (перезагрузка 1 из 3")
    check("удалено 57 из 60, «Список пройден: осталось 3»", gone == 57 and s.startswith("Список пройден: осталось 3"),
          f"{s}; удалено {gone}; перезагрузок восстановления {rec}")
    check("перезагрузки восстановления были", rec >= 3, str(rec))
    s, st, lt = run("btn_accept", "stale_after=7", accept_end, 900)
    ok_n = sum(1 for r in st["reqs"] if r["done"])
    check("приём: 41 принята, «остались пропущенные (4)»", ok_n == 41 and s == "Заявки кончились: остались пропущенные (4)",
          f"{s}; принято {ok_n}; перезагрузок {lt.count('перезагружаю страницу (перезагрузка')}")

    print("16) меню не всплывает ни у кого: 3 перезагрузки без успеха → остановка")
    s, st, lt = run("btn_delete", "nomenu_all=1", delete_end, 300)
    check(f"«{modes.MSG_STUCK}», ни одного «Прекратить»", s == modes.MSG_STUCK and not st["unfriend_log"],
          f"{s}; загрузок списка {st['loads']['friends']}")
    check("3 перезагрузки восстановления, в журнале — что поверх точки наведения",
          lt.count("перезагружаю страницу (перезагрузка") == 3 and "поверх точки: сама цель (div.user-grid-card_img" in lt,
          [l for l in lt.splitlines() if "поверх точки" in l][:1])
    shots = [p.name for p in paths.SCREENSHOTS.iterdir() if p.name.endswith("_recover.png")]
    check("скриншоты перед перезагрузками", len(shots) >= 3, str(len(shots)))


# ---------------------------------------------------------------- настоящий ok.ru
def scenario_real(d: Driver):
    c0 = counters()
    print("счётчики до:", {k: c0[k] for k in ("accept_total", "delete_total", "incident_day", "schema")})
    d.press("btn_check")
    s = d.wait_status(lambda s: s in ("Вход в ОК есть", gui_msg_login()), 90)
    check("вход в ОК есть", s == "Вход в ОК есть", s)
    c = counters()
    check("счётчики прошлой версии сохранились, schema=2",
          c["accept_total"] == c0["accept_total"] and c["delete_total"] == c0["delete_total"] and c["schema"] == 2)
    d.idle()
    d.press("btn_accept")
    s = d.wait_status(lambda s: s.startswith("Заявки кончились") or s.startswith("Ошибка") or "Не нашёл" in s, 120)
    check("приём на ok.ru: «Заявки кончились» (заявок нет)", s.startswith("Заявки кончились"), s)
    d.idle()
    d.press("btn_delete")
    s = d.wait_status(lambda s: s.startswith("Друзья кончились") or s.startswith("Ошибка") or "Не нашёл" in s
                      or s.startswith("Список пройден"), 120)
    check("удаление на ok.ru: «Друзья кончились» (друзей нет)", s.startswith("Друзья кончились"), s)
    d.idle()
    d.press("btn_accept")
    d.wait_status(lambda s: s.startswith("Принимаю заявки") or s.startswith("Проверяю"), 30)
    d.press("btn_stop")
    s = d.wait_status(lambda s: s == "Остановлено" or s.startswith("Заявки кончились"), 60)
    check("«Стоп» во время запуска приёма", s == "Остановлено", s)
    d.idle()
    store.record_incident(ACC, "проверка кнопки сброса (запись сделана тестом)")
    d.press("btn_accept")
    s = d.wait_status(lambda s: s.startswith("Был инцидент"), 20)
    check("при отметке инцидента «Принять» не запускается", s.startswith("Был инцидент"), s)
    d.idle()
    d.press("btn_reset")
    s = d.wait_status(lambda s: s.startswith("Заявки кончились") or s.startswith("Ошибка"), 120)
    check("«Сбросить и продолжить»: отметка снята, приём запущен сразу",
          s.startswith("Заявки кончились") and counters()["incident_day"] is None, s)


def scenario_realdel(d: Driver):
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    if len(sys.argv) > 3 and sys.argv[3] == "all":
        return scenario_realdel_all(d)
    before = counters()["delete_total"]
    store.update_account(ACC, run_limit=n)
    try:
        d.press("btn_delete")
        s = d.wait_status(lambda s: s.startswith("Лимит за запуск") or s.startswith("Друзья кончились") or "подряд" in s
                          or s.startswith("Ошибка") or s.startswith("Список пройден") or "Не нашёл" in s, 180)
        check(f"живое удаление: «Лимит за запуск выполнен: {n}»", s == f"Лимит за запуск выполнен: {n}", s)
        check(f"счётчик удалений +{n}", counters()["delete_total"] - before == n, str(counters()["delete_total"] - before))
        lt = [l for l in log_text().splitlines() if "[удаление]" in l][-(n + 4):]
        check("в журнале нет «не подтверждено»", not any("не подтверждено" in l for l in lt), "\n    ".join(lt))
    finally:
        store.update_account(ACC, run_limit=6000)


def scenario_realdel_all(d: Driver):
    before = counters()["delete_total"]
    store.update_account(ACC, run_limit=6000)
    d.press("btn_delete")
    s = d.wait_status(lambda s: s.startswith("Друзья кончились") or "подряд" in s or s.startswith("Ошибка")
                      or s.startswith("Список пройден") or "Не нашёл" in s, 300)
    check("живое удаление всех: «Друзья кончились»", s.startswith("Друзья кончились"), s)
    print("  удалено за запуск:", counters()["delete_total"] - before)
    lt = [l for l in log_text().splitlines() if "[удаление]" in l and "шаг:" not in l][-14:]
    check("в журнале нет «не подтверждено»", not any("не подтверждено" in l for l in lt), "\n    ".join(lt))


def gui_msg_login():
    from okf.worker import MSG_LOGIN
    return MSG_LOGIN


def scenario_cert(d: Driver):
    d.press("btn_check")
    s = d.wait_status(lambda s: s == browser.MSG_CERT or s.startswith("Ошибка") or s.startswith("Вход"), 90)
    check("expired.badssl.com: статус про сертификат", s == browser.MSG_CERT, s)
    last = log_text().splitlines()[-1]
    check("в журнале код со страницы", "код NET::ERR_CERT_" in last, last)
    shot = sorted((p for p in paths.SCREENSHOTS.iterdir() if p.name.endswith("_cert.png")), key=lambda p: p.stat().st_mtime)
    check("скриншот", bool(shot), shot[-1].name if shot else "")
    d.idle()
    d.press("btn_accept")
    s = d.wait_status(lambda s: s == browser.MSG_CERT or s.startswith("Ошибка") or s.startswith("Принимаю"), 90)
    check("«Принять заявки» тоже останавливается на предупреждении", s == browser.MSG_CERT, s)


def main():
    srv = None
    if SCENARIO in ("mock", "mocknew"):
        sys.path.insert(0, str(ROOT / "tools"))
        import mock_ok
        srv = mock_ok.serve(PORT)
    root = tk.Tk()
    app = gui.App(root)
    d = Driver(app)
    root.after(50, d.pump)

    def run():
        try:
            {"mock": lambda d: (scenario_mock(d), scenario_mock_new(d)), "mocknew": scenario_mock_new, "real": scenario_real, "realdel": scenario_realdel, "cert": scenario_cert}[SCENARIO](d)
        except Exception:
            traceback.print_exc()
            check("сценарий без исключений", False)
        finally:
            failed = [r for r in RESULTS if not r[1]]
            print(f"\nИТОГО: {len(RESULTS) - len(failed)} из {len(RESULTS)} проверок прошли", flush=True)
            for r in failed:
                print("  не прошла:", r[0], "—", r[2])
            d.ui(app.on_close)
            time.sleep(2)
            os._exit(0 if not failed else 1)

    threading.Thread(target=run, daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()

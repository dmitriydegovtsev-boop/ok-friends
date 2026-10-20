"""Окно программы (tkinter)."""
import os
import queue
import random
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox

from . import paths, store
from .worker import AccountDriver

START_OPTIONS = ["7:00", "7:30", "8:00", "8:30", "9:00", "9:30", "10:00", "10:30", "11:00", "11:30", "12:00"]
END_OPTIONS = ["20:00", "20:30", "21:00", "21:30", "22:00", "22:30", "23:00", "23:30", "0:00", "0:30", "1:00"]
HIGH_PACE_TEXT = "Высокий темп повышает риск блокировки аккаунта в ОК. Продолжить?"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("OK-Friends — заявки и друзья в Одноклассниках")
        root.geometry("980x640")
        root.minsize(860, 560)
        paths.ensure_dirs()
        self.events = queue.Queue()
        self.drivers: dict[str, AccountDriver] = {}
        self.timers: list[threading.Timer] = []
        self.current: str | None = None
        self._loading = False
        self._build()
        self._load_accounts()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(200, self._poll)
        root.after(30000, self._tick_day)
        self._autostart()

    def _autostart(self):
        """Служебный автозапуск для проверок: OKF_AUTO="Имя аккаунта:accept|delete|check"."""
        auto = os.environ.get("OKF_AUTO", "") if paths.test_hooks_enabled() else ""
        if ":" not in auto:
            return
        name, mode = auto.rsplit(":", 1)
        if name not in store.account_names():
            store.update_account(name)
            self._load_accounts()
        idx = store.account_names().index(name)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self._select_from_list()
        self.root.after(1000, lambda: self.start(mode, name))

    # ---------- интерфейс ----------
    def _build(self):
        root = self.root
        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(left, text="Аккаунты").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=22, height=18, exportselection=False)
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self._select_from_list())
        lb = ttk.Frame(left)
        lb.pack(fill="x", pady=4)
        ttk.Button(lb, text="Добавить", command=self.add_account).pack(side="left", fill="x", expand=True)
        ttk.Button(lb, text="Убрать из списка", command=self.remove_account).pack(side="left", fill="x", expand=True)

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True)
        self.title_var = tk.StringVar(value="Выберите аккаунт слева или добавьте новый")
        ttk.Label(right, textvariable=self.title_var, font=("", 13, "bold")).pack(anchor="w")

        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=4)
        self.btn_accept = ttk.Button(btns, text="Принять заявки", command=lambda: self.start("accept"))
        self.btn_accept.pack(side="left", padx=2)
        self.btn_delete = ttk.Button(btns, text="Удалить друзей", command=lambda: self.start("delete"))
        self.btn_delete.pack(side="left", padx=2)
        self.btn_stop = ttk.Button(btns, text="Стоп", command=self.stop_current)
        self.btn_stop.pack(side="left", padx=2)
        self.btn_check = ttk.Button(btns, text="Открыть браузер / проверить вход", command=lambda: self.start("check"))
        self.btn_check.pack(side="left", padx=8)

        sl = ttk.Frame(right)
        sl.pack(fill="x", pady=2)
        self.accept_scale = self._scale(sl, "Принятий в день", 0)
        self.delete_scale = self._scale(sl, "Удалений в день", 1)
        sl.columnconfigure(1, weight=1)

        opts = ttk.Frame(right)
        opts.pack(fill="x", pady=2)
        self.ramp_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Разгон (рекомендуется)", variable=self.ramp_var,
                        command=self._save_fields).pack(side="left")
        ttk.Label(opts, text="   Рабочее время с").pack(side="left")
        self.start_var = tk.StringVar(value="9:00")
        self.start_box = ttk.Combobox(opts, textvariable=self.start_var, values=START_OPTIONS, width=6, state="readonly")
        self.start_box.pack(side="left")
        ttk.Label(opts, text=" до ").pack(side="left")
        self.end_var = tk.StringVar(value="23:00")
        self.end_box = ttk.Combobox(opts, textvariable=self.end_var, values=END_OPTIONS, width=6, state="readonly")
        self.end_box.pack(side="left")
        self.start_box.bind("<<ComboboxSelected>>", lambda e: self._save_fields())
        self.end_box.bind("<<ComboboxSelected>>", lambda e: self._save_fields())

        info = ttk.Frame(right)
        info.pack(fill="x", pady=4)
        self.accept_info = tk.StringVar(value="Принято: сегодня 0 / всего 0")
        self.delete_info = tk.StringVar(value="Удалено: сегодня 0 / всего 0")
        self.ramp_info = tk.StringVar(value="Планка разгона: 250")
        ttk.Label(info, textvariable=self.accept_info).pack(anchor="w")
        ttk.Label(info, textvariable=self.delete_info).pack(anchor="w")
        ttk.Label(info, textvariable=self.ramp_info, wraplength=640, justify="left").pack(anchor="w")

        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(right, text="Статус:").pack(anchor="w", pady=(6, 0))
        ttk.Label(right, textvariable=self.status_var, foreground="#004c99", wraplength=640,
                  justify="left", font=("", 11, "bold")).pack(anchor="w")

        ttk.Label(right, text="Журнал:").pack(anchor="w", pady=(6, 0))
        logf = ttk.Frame(right)
        logf.pack(fill="both", expand=True)
        self.log_text = tk.Text(logf, height=10, wrap="word", state="disabled")
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        bottom = ttk.Frame(root, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Запустить всё: принимать", command=self.start_all).pack(side="left")
        ttk.Button(bottom, text="Стоп всё", command=self.stop_all).pack(side="left", padx=6)
        self.all_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.all_var).pack(side="left", padx=10)

    def _scale(self, parent, label, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6))
        var = tk.IntVar(value=250)
        scale = tk.Scale(parent, from_=store.SLIDER_MIN, to=store.SLIDER_MAX, resolution=store.SLIDER_STEP,
                         orient="horizontal", variable=var, showvalue=False, length=420)
        scale.grid(row=row, column=1, sticky="ew")
        val = ttk.Label(parent, textvariable=var, width=6)
        val.grid(row=row, column=2, sticky="w")
        scale.bind("<ButtonRelease-1>", lambda e: self._on_scale_release(var))
        scale.bind("<KeyRelease>", lambda e: self._on_scale_release(var))
        scale.var = var
        return scale

    # ---------- аккаунты ----------
    def _load_accounts(self):
        self.listbox.delete(0, "end")
        for name in store.account_names():
            self.listbox.insert("end", name)
        if self.listbox.size() > 0:
            self.listbox.selection_set(0)
            self._select_from_list()
        else:
            self._enable_right(False)

    def _enable_right(self, on: bool):
        state = "normal" if on else "disabled"
        for w in (self.btn_accept, self.btn_delete, self.btn_stop, self.btn_check, self.accept_scale,
                  self.delete_scale, self.start_box, self.end_box):
            try:
                w.configure(state=state if w not in (self.start_box, self.end_box) or not on else "readonly")
            except tk.TclError:
                pass

    def _select_from_list(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0])
        self.current = name
        self._enable_right(True)
        self._show_account(name)

    def _show_account(self, name: str):
        acc = store.account_settings(name)
        self._loading = True
        self.title_var.set(f"Аккаунт: {name}")
        self.accept_scale.var.set(int(acc["accept_limit"]))
        self.delete_scale.var.set(int(acc["delete_limit"]))
        self.ramp_var.set(bool(acc["ramp"]))
        self.start_var.set(acc["work_start"])
        self.end_var.set(acc["work_end"])
        self._loading = False
        self._refresh_counters(name)
        self.status_var.set(self._status.get(name, "Готово"))
        self._show_log(name)

    _status: dict = {}

    def _ask_name(self) -> str | None:
        """Своё окно ввода имени: стандартный simpledialog падает на маке с Tk 9."""
        win = tk.Toplevel(self.root)
        win.title("Новый аккаунт")
        win.resizable(False, False)
        win.transient(self.root)
        result = {"name": None}
        ttk.Label(win, text="Имя аккаунта (как угодно, например «Аккаунт 1»):", padding=(12, 12, 12, 4)).pack(anchor="w")
        var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=var, width=32)
        entry.pack(padx=12, pady=4)
        row = ttk.Frame(win, padding=(12, 8, 12, 12))
        row.pack(fill="x")

        def ok(*_):
            result["name"] = var.get()
            win.destroy()

        ttk.Button(row, text="ОК", command=ok).pack(side="right")
        ttk.Button(row, text="Отмена", command=win.destroy).pack(side="right", padx=6)
        entry.bind("<Return>", ok)
        win.bind("<Escape>", lambda e: win.destroy())
        win.update_idletasks()
        x = self.root.winfo_rootx() + 200
        y = self.root.winfo_rooty() + 150
        win.geometry(f"+{x}+{y}")
        win.grab_set()
        entry.focus_set()
        self.root.wait_window(win)
        return result["name"]

    def add_account(self):
        name = self._ask_name()
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in store.account_names():
            messagebox.showinfo("Аккаунт", "Такое имя уже есть в списке")
            return
        store.update_account(name)
        self._load_accounts()
        idx = store.account_names().index(name)
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self._select_from_list()

    def remove_account(self):
        name = self.current
        if not name:
            return
        if not messagebox.askyesno("Убрать из списка",
                                   f"Убрать «{name}» из списка? Профиль браузера, журналы и счётчики остаются на диске."):
            return
        drv = self.drivers.pop(name, None)
        if drv:
            drv.quit()
        store.remove_account(name)
        self.current = None
        self._load_accounts()
        if not self.current:
            self.title_var.set("Выберите аккаунт слева или добавьте новый")

    # ---------- поля настроек ----------
    def _on_scale_release(self, var):
        if self._loading or not self.current:
            return
        value = int(var.get())
        acc = store.account_settings(self.current)
        if value > store.HIGH_PACE and not acc.get("warned_high"):
            if messagebox.askokcancel("Высокий темп", HIGH_PACE_TEXT, icon="warning", parent=self.root):
                store.update_account(self.current, warned_high=True)
            else:
                var.set(store.HIGH_PACE)
        self._save_fields()

    def _save_fields(self):
        if self._loading or not self.current:
            return
        store.update_account(
            self.current,
            accept_limit=int(self.accept_scale.var.get()),
            delete_limit=int(self.delete_scale.var.get()),
            ramp=bool(self.ramp_var.get()),
            work_start=self.start_var.get(),
            work_end=self.end_var.get(),
        )
        self._refresh_counters(self.current)

    # ---------- запуск ----------
    def _driver(self, name: str) -> AccountDriver:
        drv = self.drivers.get(name)
        if drv is None or not drv.is_alive():
            drv = AccountDriver(name, self)
            drv.start()
            self.drivers[name] = drv
        return drv

    def start(self, mode: str, name: str | None = None):
        name = name or self.current
        if not name:
            return
        self._driver(name).request(mode)

    def stop_current(self):
        if self.current and self.current in self.drivers:
            self.drivers[self.current].stop()
            self._set_status(self.current, "Останавливаю…")

    def start_all(self):
        names = store.account_names()
        if not names:
            return
        self.stop_timers()
        for name in names:
            delay = random.uniform(60, 600)
            t = threading.Timer(delay, lambda n=name: self.root.after(0, lambda: self.start("accept", n)))
            t.daemon = True
            t.start()
            self.timers.append(t)
            self.post(name, "log", store.append_log(name, "система", f"«Запустить всё»: старт через {int(delay // 60)} мин {int(delay % 60)} с"))
        self.all_var.set(f"Запуск всех ({len(names)}) со сдвигом 1–10 мин")

    def stop_timers(self):
        for t in self.timers:
            t.cancel()
        self.timers.clear()

    def stop_all(self):
        self.stop_timers()
        for drv in self.drivers.values():
            drv.stop()
        self.all_var.set("Стоп всё")

    # ---------- сообщения из потоков ----------
    def post(self, account: str, kind: str, payload):
        self.events.put((account, kind, payload))

    def _poll(self):
        try:
            while True:
                account, kind, payload = self.events.get_nowait()
                if kind == "status":
                    self._set_status(account, payload)
                elif kind == "log":
                    if account == self.current:
                        self._append_log_line(payload)
                elif kind == "counters":
                    if account == self.current:
                        self._refresh_counters(account)
                elif kind == "no_chrome":
                    self._no_chrome_window()
        except queue.Empty:
            pass
        self.root.after(200, self._poll)

    def _tick_day(self):
        if self.current:
            self._refresh_counters(self.current)
        self.root.after(30000, self._tick_day)

    def _set_status(self, account: str, text: str):
        self._status[account] = text
        if account == self.current:
            self.status_var.set(text)

    def _refresh_counters(self, name: str):
        acc = store.account_settings(name)
        c = store.rollover(name, acc)
        self.accept_info.set(f"Принято: сегодня {c['accept_today']} / всего {c['accept_total']}")
        self.delete_info.set(f"Удалено: сегодня {c['delete_today']} / всего {c['delete_total']}")
        if acc.get("ramp", True):
            cap_a = store.effective_cap(name, "accept", acc)
            cap_d = store.effective_cap(name, "delete", acc)
            self.ramp_info.set(f"Планка разгона: {c['ramp_cap']} (сегодня приём до {cap_a}, удаление до {cap_d}). "
                               f"Последнее изменение: {c['ramp_reason']}")
        else:
            self.ramp_info.set(f"Разгон выключен: планка = бегунок (приём {acc['accept_limit']}, удаление {acc['delete_limit']})")

    def _show_log(self, name: str):
        p = paths.LOGS / f"{paths.safe_name(name)}.txt"
        lines = []
        if p.exists():
            try:
                lines = p.read_text(encoding="utf-8").splitlines()[-200:]
            except OSError:
                pass
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(lines) + ("\n" if lines else ""))
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _append_log_line(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")

    def _no_chrome_window(self):
        win = tk.Toplevel(self.root)
        win.title("Нужен Google Chrome")
        win.resizable(False, False)
        ttk.Label(win, text="Установите Google Chrome", font=("", 14, "bold"), padding=16).pack()
        link = ttk.Label(win, text="google.com/chrome", foreground="blue", cursor="hand2", padding=(16, 0, 16, 16))
        link.pack()
        link.bind("<Button-1>", lambda e: webbrowser.open("https://www.google.com/chrome"))
        win.transient(self.root)
        win.grab_set()

    def on_close(self):
        self.stop_timers()
        for drv in self.drivers.values():
            drv.quit()
        self.root.after(300, self.root.destroy)


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam" if root.tk.call("tk", "windowingsystem") != "aqua" else "aqua")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()

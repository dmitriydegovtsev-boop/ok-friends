"""Окно программы (tkinter)."""
import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, messagebox

from . import paths, store
from .worker import AccountDriver



class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("OK-Friends v0.2.0 — заявки и друзья в Одноклассниках")
        root.geometry("980x620")
        root.minsize(860, 560)
        paths.ensure_dirs()
        self.events = queue.Queue()
        self.drivers: dict[str, AccountDriver] = {}
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
        self.btn_reset = ttk.Button(btns, text="Сбросить и продолжить", command=lambda: self.start("reset"))
        self.btn_reset.pack(side="left", padx=2)
        self.btn_check = ttk.Button(btns, text="Открыть браузер / проверить вход", command=lambda: self.start("check"))
        self.btn_check.pack(side="left", padx=8)

        opts = ttk.Frame(right)
        opts.pack(fill="x", pady=4)
        ttk.Label(opts, text="Лимит за запуск").pack(side="left")
        self.limit_var = tk.StringVar(value=str(store.RUN_LIMIT_MAX))
        self.limit_box = ttk.Spinbox(opts, from_=1, to=store.RUN_LIMIT_MAX, increment=100, width=7,
                                     textvariable=self.limit_var, command=self._save_fields)
        self.limit_box.pack(side="left", padx=6)
        self.limit_box.bind("<FocusOut>", lambda e: self._save_fields())
        self.limit_box.bind("<Return>", lambda e: self._save_fields())
        ttk.Label(opts, text=f"(1–{store.RUN_LIMIT_MAX}; одно действие = одна заявка или один друг)").pack(side="left")

        info = ttk.Frame(right)
        info.pack(fill="x", pady=4)
        self.accept_info = tk.StringVar(value="Принято: сегодня 0 / всего 0")
        self.delete_info = tk.StringVar(value="Удалено: сегодня 0 / всего 0")
        ttk.Label(info, textvariable=self.accept_info).pack(anchor="w")
        ttk.Label(info, textvariable=self.delete_info).pack(anchor="w")

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
        for w in (self.btn_accept, self.btn_delete, self.btn_stop, self.btn_reset, self.btn_check, self.limit_box):
            try:
                w.configure(state=state)
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
        self.limit_var.set(str(store.run_limit(acc)))
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
    def _save_fields(self):
        if self._loading or not self.current:
            return
        try:
            value = int(str(self.limit_var.get()).strip().replace(" ", ""))
        except ValueError:
            value = store.run_limit(store.account_settings(self.current))
        value = store.run_limit({"run_limit": value})
        self.limit_var.set(str(value))
        store.update_account(self.current, run_limit=value)

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
        if name == self.current:
            self._save_fields()     # лимит, введённый без Enter, тоже учитываем
        self._driver(name).request(mode)

    def stop_current(self):
        if self.current and self.current in self.drivers:
            self.drivers[self.current].stop()
            self._set_status(self.current, "Останавливаю…")

    def start_all(self):
        """Все аккаунты сразу, каждый в своём Chrome."""
        names = store.account_names()
        for name in names:
            self.post(name, "log", store.append_log(name, "система", "«Запустить всё»: старт приёма"))
            self.start("accept", name)
        self.all_var.set(f"Запущено аккаунтов: {len(names)}" if names else "")

    def stop_all(self):
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
        c = store.rollover(name)
        self.accept_info.set(f"Принято: сегодня {c['accept_today']} / всего {c['accept_total']}")
        self.delete_info.set(f"Удалено: сегодня {c['delete_today']} / всего {c['delete_total']}")

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

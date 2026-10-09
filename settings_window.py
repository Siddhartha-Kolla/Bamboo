"""The settings window: accounts (Apple, Google), defaults and general options.

A normal Toplevel on the popup's own Tk loop, created when asked for and destroyed on close, so it
costs nothing while idle. Every button calls a method on SettingsWindow that only touches settings.py,
vault.py, icloud.py and google_cal.py, so each action can be driven (and tested) without clicking.

Secrets typed here go straight into the passphrase vault; the window never shows or logs them. Network
actions run on a worker thread and report back through a queue that the window polls.
"""
import ctypes
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

import calendars
import settings
import theme
import vault
from theme import ACCENT, BG, BORDER, CARD, DIM, ERR, FG, FIELD, OK, WARN

_open = {"win": None}


def open_window(popup):
    """Open the settings window, or bring the one that is already open to the front."""
    win = _open["win"]
    if win is not None and win.alive():
        win.top.deiconify()
        win.top.lift()
        win.top.focus_force()
        return win
    _open["win"] = SettingsWindow(popup)
    return _open["win"]


def ask_passphrase(parent, title, prompt, confirm=False):
    """A small modal box. Returns the passphrase, or None if cancelled. Tests replace this function."""
    result = {"value": None}
    box = tk.Toplevel(parent)
    box.title(title)
    box.configure(bg=BG, padx=24, pady=20)
    box.transient(parent)
    box.resizable(False, False)
    tk.Label(box, text=prompt, bg=BG, fg=FG, font=(theme.FONT, 11), justify="left", wraplength=360).pack(anchor="w")
    first = tk.Entry(box, show="•", bg=FIELD, fg=FG, insertbackground=ACCENT, relief="flat", font=(theme.FONT, 12), width=34)
    first.pack(fill="x", pady=(12, 0), ipady=5)
    second = None
    if confirm:
        second = tk.Entry(box, show="•", bg=FIELD, fg=FG, insertbackground=ACCENT, relief="flat", font=(theme.FONT, 12), width=34)
        tk.Label(box, text="Repeat it:", bg=BG, fg=DIM, font=(theme.FONT, 10)).pack(anchor="w", pady=(10, 0))
        second.pack(fill="x", pady=(4, 0), ipady=5)
    note = tk.Label(box, text="", bg=BG, fg=ERR, font=(theme.FONT, 10))
    note.pack(anchor="w", pady=(8, 0))

    def ok(_event=None):
        value = first.get()
        if confirm:
            if len(value) < vault.MIN_PASSPHRASE:
                note.config(text="Use at least %d characters." % vault.MIN_PASSPHRASE)
                return
            if second.get() != value:
                note.config(text="They do not match.")
                return
        result["value"] = value
        box.destroy()

    buttons = tk.Frame(box, bg=BG)
    buttons.pack(fill="x", pady=(10, 0))
    tk.Button(buttons, text="OK", command=ok, bg=ACCENT, fg=BG, relief="flat", bd=0, padx=18, pady=4,
              font=(theme.FONT, 10, "bold"), cursor="hand2").pack(side="right")
    tk.Button(buttons, text="Cancel", command=box.destroy, bg=CARD, fg=FG, relief="flat", bd=0, padx=14, pady=4,
              font=(theme.FONT, 10), cursor="hand2").pack(side="right", padx=(0, 8))
    box.bind("<Return>", ok)
    box.bind("<Escape>", lambda e: box.destroy())
    box.update_idletasks()
    box.geometry("+%d+%d" % (parent.winfo_rootx() + 60, parent.winfo_rooty() + 80))
    box.grab_set()
    first.focus_set()
    parent.wait_window(box)
    return result["value"]


class SettingsWindow:
    BASE_WIDTH, BASE_HEIGHT = 700, 800

    def __init__(self, popup):
        self.popup = popup
        self.root = popup.root
        self.k = popup.k
        self.q = queue.Queue()
        self.cancel = None
        self.armed = {}                                    # destructive buttons waiting for a second click
        self.apple_connected = False

        self.top = top = tk.Toplevel(self.root)
        top.title("Quick add – Settings")
        top.configure(bg=BG)
        height = min(self.px(self.BASE_HEIGHT), int(top.winfo_screenheight() * 0.9))      # never taller than the screen
        top.geometry("%dx%d" % (self.px(self.BASE_WIDTH), height))
        top.minsize(self.px(560), self.px(480))
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda e: self.close())
        self._dark_title_bar()
        self._style()

        tk.Label(top, text="Settings", bg=BG, fg=FG, font=(theme.FONT, 18, "bold")).pack(anchor="w", padx=self.px(24), pady=(self.px(18), self.px(6)))
        self.tabs = ttk.Notebook(top, style="Q.TNotebook")
        self.tabs.pack(fill="both", expand=True, padx=self.px(16), pady=(0, self.px(16)))
        self._build_accounts()
        self._build_defaults()
        self._build_general()
        self.reload()
        top.after(100, self._pump)
        top.focus_force()

    # ------------------------------------------------------------------ plumbing

    def px(self, n):
        return int(round(n * self.k))

    def alive(self):
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.cancel is not None:
            self.cancel.set()
        try:
            self.top.destroy()
        finally:
            _open["win"] = None

    def _dark_title_bar(self):
        try:                                               # Windows 10/11: dark caption to match the theme
            self.top.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.top.winfo_id())
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    def _style(self):
        style = ttk.Style(self.top)
        style.theme_use("clam")
        style.configure("Q.TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0),
                        bordercolor=BG, lightcolor=BG, darkcolor=BG)
        style.configure("Q.TNotebook.Tab", background=BG, foreground=DIM, padding=(self.px(18), self.px(8)),
                        borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, focuscolor=BG, font=(theme.FONT, 11))
        style.map("Q.TNotebook.Tab", background=[("selected", CARD), ("active", FIELD)], foreground=[("selected", FG), ("active", FG)],
                  bordercolor=[("selected", CARD), ("!selected", BG)], lightcolor=[("selected", CARD), ("!selected", BG)],
                  darkcolor=[("selected", CARD), ("!selected", BG)], focuscolor=[("selected", CARD), ("!selected", BG)])
        style.configure("Q.TCombobox", fieldbackground=FIELD, background=FIELD, foreground=FG, arrowcolor=FG,
                        bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD, selectbackground=FIELD, selectforeground=FG,
                        padding=self.px(5))
        style.map("Q.TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        self.top.option_add("*TCombobox*Listbox.background", FIELD)
        self.top.option_add("*TCombobox*Listbox.foreground", FG)
        self.top.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.top.option_add("*TCombobox*Listbox.selectForeground", BG)

    def run_async(self, work, done):
        """Run work() on a thread; done(result, error) is then called on the Tk thread."""
        def target():
            try:
                self.q.put((done, work(), None))
            except Exception as err:  # noqa: BLE001 - shown to the user, never raised into Tk
                self.q.put((done, None, err))
        threading.Thread(target=target, daemon=True).start()

    def _pump(self):
        if not self.alive():
            return
        try:
            while True:
                done, result, error = self.q.get_nowait()
                done(result, error)
        except queue.Empty:
            pass
        self.top.after(100, self._pump)

    def say(self, label, text, kind="info"):
        label.config(text=text, fg={"ok": OK, "error": ERR, "warn": WARN, "info": DIM}[kind])

    def confirm_twice(self, key, button, label, action):
        """First click arms the button, a second click within 4 s runs it (no pop-up dialogs)."""
        if self.armed.get(key):
            self.armed[key] = False
            button.config(text=label)
            action()
            return
        self.armed[key] = True
        button.config(text="Click again to confirm")

        def disarm():
            if self.armed.get(key) and self.alive():
                self.armed[key] = False
                button.config(text=label)
        self.top.after(4000, disarm)

    def ensure_unlocked(self, status_label):
        """True when the vault can be written: asks for the passphrase, or creates the vault the first time."""
        if vault.is_unlocked():
            return True
        if vault.has_vault():
            for _ in range(3):
                phrase = ask_passphrase(self.top, "Unlock", "Enter your passphrase to continue.")
                if phrase is None:
                    return False
                if vault.unlock(phrase):
                    return True
                self.say(status_label, "Wrong passphrase.", "error")
            return False
        phrase = ask_passphrase(self.top, "Create a passphrase",
                                "Your calendar passwords are stored encrypted. Choose a passphrase that only you know; "
                                "you will be asked for it again after %d minutes without use." % (vault.UNLOCK_IDLE_SECONDS // 60),
                                confirm=True)
        if phrase is None:
            return False
        vault.create(phrase)
        return True

    # ------------------------------------------------------------------ widget helpers

    def _page(self, title):
        page = tk.Frame(self.tabs, bg=CARD)
        self.tabs.add(page, text=title)
        inner = tk.Frame(page, bg=CARD)
        inner.pack(fill="both", expand=True, padx=self.px(22), pady=self.px(16))
        inner.columnconfigure(1, weight=1)
        return inner

    def _heading(self, parent, row, text):
        tk.Label(parent, text=text, bg=CARD, fg=FG, font=(theme.FONT, 13, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(self.px(14) if row else 0, self.px(6)))

    def _field(self, parent, row, label, var, show=None, width=30):
        tk.Label(parent, text=label, bg=CARD, fg=DIM, font=(theme.FONT, 10)).grid(row=row, column=0, sticky="w", pady=self.px(4), padx=(0, self.px(12)))
        entry = tk.Entry(parent, textvariable=var, show=show or "", bg=FIELD, fg=FG, insertbackground=ACCENT, relief="flat",
                         font=(theme.FONT, 11), width=width, highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                         disabledbackground=CARD, disabledforeground=BORDER)
        entry.grid(row=row, column=1, sticky="ew", pady=self.px(4), ipady=self.px(4))
        return entry

    def _combo(self, parent, row, label, var, values=()):
        tk.Label(parent, text=label, bg=CARD, fg=DIM, font=(theme.FONT, 10)).grid(row=row, column=0, sticky="w", pady=self.px(4), padx=(0, self.px(12)))
        box = ttk.Combobox(parent, textvariable=var, values=list(values), state="readonly", style="Q.TCombobox", font=(theme.FONT, 11))
        box.grid(row=row, column=1, sticky="ew", pady=self.px(4))
        return box

    def _button(self, parent, text, command, primary=False):
        return tk.Button(parent, text=text, command=command, bg=ACCENT if primary else FIELD, fg=BG if primary else FG,
                         activebackground=FG, activeforeground=BG, relief="flat", bd=0, padx=self.px(16), pady=self.px(6),
                         font=(theme.FONT, 10, "bold" if primary else "normal"), cursor="hand2")

    def _row(self, parent, row, *buttons):
        frame = tk.Frame(parent, bg=CARD)
        frame.grid(row=row, column=0, columnspan=3, sticky="w", pady=(self.px(8), 0))
        for b in buttons:
            b.pack(in_=frame, side="left", padx=(0, self.px(8)))
            b.lift()                                       # the frame was created after the buttons and would hide them

    def _status(self, parent, row):
        label = tk.Label(parent, text="", bg=CARD, fg=DIM, font=(theme.FONT, 10), justify="left", anchor="w",
                         wraplength=self.px(560))
        label.grid(row=row, column=0, columnspan=3, sticky="w", pady=(self.px(6), 0))
        return label

    # ------------------------------------------------------------------ Accounts tab

    def _build_accounts(self):
        page = self._page("Accounts")
        self._heading(page, 0, "Apple Calendar")
        self.apple_state = self._status(page, 1)
        self.apple_id_var, self.apple_pw_var, self.apple_cal_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._field(page, 2, "Apple ID", self.apple_id_var)
        self._field(page, 3, "App-specific password", self.apple_pw_var, show="•")
        self.apple_cal = self._combo(page, 4, "Calendar", self.apple_cal_var)
        self.apple_connect_btn = self._button(page, "Connect", self.apple_connect)
        self.apple_save_btn = self._button(page, "Save", self.apple_save, primary=True)
        self.apple_off_btn = self._button(page, "Disconnect", lambda: self.confirm_twice(
            "apple", self.apple_off_btn, "Disconnect", self.apple_disconnect))
        self._row(page, 5, self.apple_connect_btn, self.apple_save_btn, self.apple_off_btn)
        self.apple_msg = self._status(page, 6)

        self._heading(page, 7, "Google Calendar")
        self.google_state = self._status(page, 8)
        self.g_id_var, self.g_secret_var, self.g_cal_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._field(page, 9, "Client ID", self.g_id_var)
        self._field(page, 10, "Client secret", self.g_secret_var, show="•")
        self.g_cal = self._combo(page, 11, "Calendar", self.g_cal_var)
        self.g_signin_btn = self._button(page, "Sign in with Google", self.google_sign_in)
        self.g_save_btn = self._button(page, "Save calendar", self.google_save_calendar, primary=True)
        self.g_off_btn = self._button(page, "Disconnect", lambda: self.confirm_twice(
            "google", self.g_off_btn, "Disconnect", self.google_disconnect))
        self.g_guide_btn = self._button(page, "Setup guide", self.show_guide)
        self._row(page, 12, self.g_signin_btn, self.g_save_btn, self.g_off_btn, self.g_guide_btn)
        self.google_msg = self._status(page, 13)

    def apple_connect(self):
        apple_id, password = self.apple_id_var.get().strip(), self.apple_pw_var.get().strip()
        if apple_id and not password and vault.has_vault() and settings.apple_configured():
            if not self.ensure_unlocked(self.apple_msg):                  # reuse the stored password to reload the calendars
                return
            password = vault.get("apple_password") or ""
        if not apple_id or not password:
            self.say(self.apple_msg, "Enter your Apple ID and an app-specific password first.", "error")
            return
        self.say(self.apple_msg, "Connecting to iCloud…")
        import icloud
        self.run_async(lambda: icloud.list_calendars(apple_id, password), self._apple_connected)

    def _apple_connected(self, names, error):
        if error:
            self.say(self.apple_msg, "Could not connect: %s. Use an app-specific password (xxxx-xxxx-xxxx-xxxx), not your normal one."
                     % (str(error) or error.__class__.__name__), "error")
            return
        if not names:
            self.say(self.apple_msg, "Connected, but this account has no event calendars.", "error")
            return
        self.apple_connected = True
        self.apple_cal.config(values=names)
        current = settings.load().get("calendar")
        self.apple_cal_var.set(current if current in names else names[0])
        self.say(self.apple_msg, "Connected. Pick a calendar and press Save.", "ok")

    def apple_save(self):
        apple_id, password, calendar = self.apple_id_var.get().strip(), self.apple_pw_var.get().strip(), self.apple_cal_var.get()
        cfg = settings.load()
        if password and not self.apple_connected:
            self.say(self.apple_msg, "Press Connect first, to check the password and load your calendars.", "error")
            return
        if not calendar or not apple_id:
            self.say(self.apple_msg, "Connect first, then pick a calendar.", "error")
            return
        if not password and not settings.apple_configured(cfg):
            self.say(self.apple_msg, "Enter the app-specific password and press Connect.", "error")
            return
        if password:
            if not self.ensure_unlocked(self.apple_msg):
                self.say(self.apple_msg, "Not saved: the passphrase is needed to store the password.", "error")
                return
            vault.put("apple_password", password)
            self.apple_pw_var.set("")
            self.apple_connected = False
        settings.update({"apple_id": apple_id, "calendar": calendar})
        self.say(self.apple_msg, "Saved.", "ok")
        self.reload()

    def apple_disconnect(self):
        if vault.has_vault() and not self.ensure_unlocked(self.apple_msg):
            return
        if vault.has_vault():
            vault.delete("apple_password")
        cfg = settings.load()
        cfg.pop("apple_id", None)
        cfg.pop("calendar", None)
        settings.save(cfg)
        self.say(self.apple_msg, "Apple Calendar disconnected.", "ok")
        self.reload()

    def show_guide(self):
        import google_cal
        box = tk.Toplevel(self.top)
        box.title("Google setup guide")
        box.configure(bg=BG, padx=22, pady=18)
        tk.Label(box, text=google_cal.GUIDE, bg=BG, fg=FG, font=(theme.FONT, 10), justify="left", wraplength=self.px(560)).pack(anchor="w")
        tk.Button(box, text="Close", command=box.destroy, bg=FIELD, fg=FG, relief="flat", bd=0, padx=16, pady=5).pack(anchor="e", pady=(12, 0))

    def google_sign_in(self):
        import google_cal
        if self.cancel is not None and not self.cancel.is_set():          # the button doubles as Cancel while waiting
            self.cancel.set()
            return
        client_id, secret = self.g_id_var.get().strip(), self.g_secret_var.get().strip()
        if not client_id or not secret:
            self.say(self.google_msg, "Paste the client ID and client secret from Google Cloud first (see Setup guide).", "error")
            return
        if not self.ensure_unlocked(self.google_msg):
            self.say(self.google_msg, "Not started: the passphrase is needed to store the sign-in.", "error")
            return
        self.cancel = threading.Event()
        self.g_signin_btn.config(text="Cancel")
        self.say(self.google_msg, "Waiting for you in the browser…")
        cancel = self.cancel
        self.run_async(lambda: google_cal.sign_in(client_id, secret, cancel=cancel),
                       lambda tokens, error: self._google_signed_in(client_id, secret, tokens, error))

    def _google_signed_in(self, client_id, secret, tokens, error):
        import google_cal
        self.cancel = None
        self.g_signin_btn.config(text="Sign in with Google")
        if error:
            self.say(self.google_msg, str(error), "error")
            return
        try:
            google_cal.save_connection(client_id, secret, tokens["refresh_token"], tokens.get("access_token"), tokens.get("expires_in", 0))
        except vault.Locked:
            self.say(self.google_msg, "The vault locked while you signed in. Press Sign in again.", "error")
            return
        self.g_secret_var.set("")
        self.say(self.google_msg, "Signed in. Loading your calendars…")
        self.run_async(google_cal.list_calendars, self._google_calendars)

    def _google_calendars(self, found, error):
        if error:
            self.say(self.google_msg, "Signed in, but the calendars could not be loaded: %s" % error, "error")
            return
        self.google_found = {c["name"]: c for c in found}
        names = [c["name"] for c in found]
        self.g_cal.config(values=names)
        current = settings.load()["google"].get("calendar_name")
        primary = next((c["name"] for c in found if c["primary"]), names[0] if names else "")
        self.g_cal_var.set(current if current in names else primary)
        self.say(self.google_msg, "Signed in. Pick a calendar and press Save calendar.", "ok")
        self.reload()

    def google_save_calendar(self):
        found = getattr(self, "google_found", {})
        chosen = found.get(self.g_cal_var.get())
        if not chosen:
            self.say(self.google_msg, "Sign in first, then pick a calendar.", "error")
            return
        settings.update({"google": {"calendar_id": chosen["id"], "calendar_name": chosen["name"]}})
        self.say(self.google_msg, "Saved. Google Calendar is ready.", "ok")
        self.reload()

    def google_disconnect(self):
        import google_cal
        if not self.ensure_unlocked(self.google_msg):
            return
        self.say(self.google_msg, "Disconnecting…")
        self.run_async(google_cal.disconnect, self._google_disconnected)

    def _google_disconnected(self, _result, error):
        if error:
            self.say(self.google_msg, "Could not disconnect: %s" % error, "error")
            return
        self.google_found = {}
        self.g_cal.config(values=[])
        self.g_cal_var.set("")
        self.say(self.google_msg, "Google Calendar disconnected and its sign-in revoked.", "ok")
        self.reload()

    # ------------------------------------------------------------------ Defaults tab

    def _build_defaults(self):
        page = self._page("Defaults")
        self._heading(page, 0, "New events")
        self.target_var, self.duration_var, self.lock_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.target_box = self._combo(page, 1, "Default calendar", self.target_var)
        self.duration_spin = self._spin(page, 2, "Length when none is typed (min)", self.duration_var, 1, 1440)
        self._heading(page, 3, "Security")
        self.lock_spin = self._spin(page, 4, "Ask for the passphrase again after (min idle)", self.lock_var, 1, 1440)
        self._heading(page, 5, "Time")
        self.tz_auto_var, self.tz_var = tk.BooleanVar(), tk.StringVar()
        tk.Checkbutton(page, text="Use this PC's timezone automatically", variable=self.tz_auto_var, command=self._tz_toggle,
                       bg=CARD, fg=FG, selectcolor=FIELD, activebackground=CARD, activeforeground=FG, font=(theme.FONT, 10)).grid(
            row=6, column=0, columnspan=3, sticky="w")
        self.tz_entry = self._field(page, 7, "Timezone (e.g. Europe/Berlin)", self.tz_var)
        self._row(page, 8, self._button(page, "Save", self.save_defaults, primary=True))
        self.defaults_msg = self._status(page, 9)

    def _spin(self, parent, row, label, var, low, high):
        tk.Label(parent, text=label, bg=CARD, fg=DIM, font=(theme.FONT, 10)).grid(row=row, column=0, sticky="w", pady=self.px(4), padx=(0, self.px(12)))
        spin = tk.Spinbox(parent, from_=low, to=high, textvariable=var, width=8, bg=FIELD, fg=FG, insertbackground=ACCENT,
                          buttonbackground=CARD, relief="flat", font=(theme.FONT, 11), highlightthickness=1,
                          highlightbackground=BORDER, highlightcolor=ACCENT)
        spin.grid(row=row, column=1, sticky="w", pady=self.px(4), ipady=self.px(3))
        return spin

    def _tz_toggle(self):
        self.tz_entry.config(state="disabled" if self.tz_auto_var.get() else "normal")

    def save_defaults(self):
        try:
            duration, lock = int(self.duration_var.get()), int(self.lock_var.get())
            if not (1 <= duration <= 1440 and 1 <= lock <= 1440):
                raise ValueError
        except ValueError:
            self.say(self.defaults_msg, "Length and lock time must be whole numbers from 1 to 1440.", "error")
            return
        zone = "auto" if self.tz_auto_var.get() else self.tz_var.get().strip()
        if zone != "auto":
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(zone)
            except Exception:  # noqa: BLE001
                self.say(self.defaults_msg, "%r is not a timezone name. Use the form Europe/Berlin." % zone, "error")
                return
        target = {v: k for k, v in settings.LABELS.items()}.get(self.target_var.get())
        changes = {"defaults": {"duration_min": duration, "unlock_idle_min": lock, "timezone": zone}}
        if target:
            changes["default_target"] = target
        settings.update(changes)
        import quickadd
        quickadd.apply_settings()
        self.say(self.defaults_msg, "Saved. Applies to the next event.", "ok")
        self.reload()

    # ------------------------------------------------------------------ General tab

    def _build_general(self):
        page = self._page("General")
        self._heading(page, 0, "Hotkey")
        self.hotkey_var = tk.StringVar()
        self._field(page, 1, "Open the popup with", self.hotkey_var, width=20)
        self._row(page, 2, self._button(page, "Save hotkey", self.save_hotkey, primary=True))
        self.hotkey_msg = self._status(page, 3)
        self._heading(page, 4, "Startup")
        self.autostart_var = tk.BooleanVar()
        tk.Checkbutton(page, text="Start quick-add when I log in to Windows", variable=self.autostart_var, command=self.toggle_autostart,
                       bg=CARD, fg=FG, selectcolor=FIELD, activebackground=CARD, activeforeground=FG, font=(theme.FONT, 10)).grid(
            row=5, column=0, columnspan=3, sticky="w")
        self._heading(page, 6, "Maintenance")
        self.lock_btn = self._button(page, "Lock now", self.lock_now)
        self.log_btn = self._button(page, "Open log", self.open_log)
        self.restart_btn = self._button(page, "Restart quick-add", self.restart_app)
        self._row(page, 7, self.lock_btn, self.log_btn, self.restart_btn)
        self.general_msg = self._status(page, 8)

    def save_hotkey(self):
        text = self.hotkey_var.get().strip()
        if not settings.valid_hotkey(text):
            self.say(self.hotkey_msg, "Not a valid hotkey. Examples: ctrl+alt+C, ctrl+shift+7, alt+F9. It needs Ctrl, Alt or Win.", "error")
            return
        settings.update({"defaults": {"hotkey": text}})
        self.say(self.hotkey_msg, "Saved. Press Restart quick-add for the new hotkey to take effect.", "ok")

    def toggle_autostart(self):
        from quickadd_ctl import set_autostart
        try:
            set_autostart(self.autostart_var.get())
            self.say(self.general_msg, "Will start with Windows." if self.autostart_var.get() else "Will not start with Windows.", "ok")
        except Exception as err:  # noqa: BLE001
            self.say(self.general_msg, "Could not change it: %s" % err, "error")

    def lock_now(self):
        vault.lock()
        self.say(self.general_msg, "Locked. The passphrase is needed for the next event.", "ok")

    def open_log(self):
        import quickadd
        try:
            os.startfile(quickadd.LOG)
        except OSError:
            self.say(self.general_msg, "There is no log yet.", "warn")

    def restart_app(self):
        self.say(self.general_msg, "Restarting…")
        self.top.after(300, self.popup.restart)

    # ------------------------------------------------------------------ load saved values into the widgets

    def reload(self):
        cfg = settings.load()
        d = cfg["defaults"]
        # Apple
        if settings.apple_configured(cfg):
            self.say(self.apple_state, "Connected: %s  →  %s" % (cfg["apple_id"], cfg["calendar"]), "ok")
            self.apple_id_var.set(cfg["apple_id"])
            self.apple_cal.config(values=[cfg["calendar"]])
            self.apple_cal_var.set(cfg["calendar"])
        else:
            self.say(self.apple_state, "Not connected. Use an app-specific password from appleid.apple.com.", "info")
            self.apple_cal_var.set("")
        # Google
        g = cfg["google"]
        if settings.google_configured(cfg):
            self.say(self.google_state, "Connected: %s" % (g.get("calendar_name") or g["calendar_id"]), "ok")
            self.g_cal.config(values=[g.get("calendar_name") or g["calendar_id"]])
            self.g_cal_var.set(g.get("calendar_name") or g["calendar_id"])
        elif g.get("client_id"):
            self.say(self.google_state, "Signed in, but no calendar chosen yet.", "warn")
        else:
            self.say(self.google_state, "Not connected. Needs a one-time Google Cloud setup (see Setup guide).", "info")
        if g.get("client_id"):
            self.g_id_var.set(g["client_id"])
        # Defaults
        ready = [settings.LABELS[t] for t in settings.configured_targets(cfg)]
        self.target_box.config(values=ready, state="readonly" if ready else "disabled")
        current = settings.default_target(cfg)
        self.target_var.set(settings.LABELS[current] if current else "")
        self.duration_var.set(str(d["duration_min"]))
        self.lock_var.set(str(d["unlock_idle_min"]))
        self.tz_auto_var.set(d["timezone"] == "auto")
        self.tz_var.set("" if d["timezone"] == "auto" else d["timezone"])
        self._tz_toggle()
        # General
        self.hotkey_var.set(d["hotkey"])
        try:
            from quickadd_ctl import autostart_enabled
            self.autostart_var.set(autostart_enabled())
        except Exception:  # noqa: BLE001
            pass

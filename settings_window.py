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
import webbrowser
from tkinter import ttk

import branding
import calendars
import settings
import theme
import vault
import voice  # noqa: F401 - the switch below changes voice.enabled()
from theme import ACCENT, ACCENT_FILL, BG, BORDER, CARD, DIM, ERR, FG, FIELD, OK, ON_FILL, WARN

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
    branding.set_window_icon(box)
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
    tk.Button(buttons, text="OK", command=ok, bg=ACCENT_FILL, fg=ON_FILL, activebackground=ACCENT, activeforeground=BG, relief="flat", bd=0, padx=18, pady=4,
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
        top.title("Bamboo – Settings")
        top.configure(bg=BG)
        height = min(self.px(self.BASE_HEIGHT), int(top.winfo_screenheight() * 0.9))      # never taller than the screen
        top.geometry("%dx%d" % (self.px(self.BASE_WIDTH), height))
        top.minsize(self.px(560), self.px(480))
        top.protocol("WM_DELETE_WINDOW", self.close)
        top.bind("<Escape>", lambda e: self.close())
        self._dark_title_bar()
        self._style()
        branding.set_window_icon(top)

        header = tk.Frame(top, bg=BG)
        header.pack(fill="x", padx=self.px(24), pady=(self.px(16), self.px(6)))
        self.mascot = branding.photo("tile", self.px(52), master=top)
        if self.mascot is not None:
            tk.Label(header, image=self.mascot, bg=BG).pack(side="left", padx=(0, self.px(14)))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="Bamboo", bg=BG, fg=FG, font=(theme.FONT, 18, "bold")).pack(anchor="w")
        tk.Label(titles, text="Settings", bg=BG, fg=DIM, font=(theme.FONT, 11)).pack(anchor="w")
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
        self.top.option_add("*TCombobox*Listbox.selectBackground", ACCENT_FILL)
        self.top.option_add("*TCombobox*Listbox.selectForeground", ON_FILL)

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
        return tk.Button(parent, text=text, command=command, bg=ACCENT_FILL if primary else FIELD, fg=ON_FILL if primary else FG,
                         activebackground=ACCENT, activeforeground=BG, relief="flat", bd=0, padx=self.px(16), pady=self.px(6),
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
        self.g_own_var = tk.BooleanVar()                          # True: sign in with the user's own Google project
        self.g_own_touched = False
        self.g_adv_open, self.g_adv_touched = False, False
        self.g_cal = self._combo(page, 9, "Calendar", self.g_cal_var)
        self.g_signin_btn = self._button(page, "Sign in with Google", self.google_sign_in, primary=True)
        self.g_save_btn = self._button(page, "Save calendar", self.google_save_calendar)
        self.g_off_btn = self._button(page, "Disconnect", lambda: self.confirm_twice(
            "google", self.g_off_btn, "Disconnect", self.google_disconnect))
        self._row(page, 10, self.g_signin_btn, self.g_save_btn, self.g_off_btn)
        self.google_msg = self._status(page, 11)

        self.g_adv_btn = tk.Button(page, text="", command=self.toggle_advanced, bg=CARD, fg=DIM, activebackground=CARD,
                                   activeforeground=FG, relief="flat", bd=0, font=(theme.FONT, 10), cursor="hand2", anchor="w")
        self.g_adv_btn.grid(row=12, column=0, columnspan=3, sticky="w", pady=(self.px(10), 0))
        self.g_adv = tk.Frame(page, bg=CARD)
        self.g_adv.grid(row=13, column=0, columnspan=3, sticky="ew")
        self.g_adv.columnconfigure(1, weight=1)
        self.g_own_btn = tk.Button(self.g_adv, text="", command=self.toggle_own, bg=CARD, fg=DIM, activebackground=CARD, activeforeground=FG,
                                   relief="flat", bd=0, font=(theme.FONT, 10), cursor="hand2", anchor="w")
        self.g_own_btn.grid(row=0, column=0, columnspan=3, sticky="w")
        self.g_fields = tk.Frame(self.g_adv, bg=CARD)
        self.g_fields.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.g_fields.columnconfigure(1, weight=1)
        self._field(self.g_fields, 0, "Client ID", self.g_id_var)
        self._field(self.g_fields, 1, "Client secret", self.g_secret_var, show="\u2022")
        self.g_guide_btn = self._button(self.g_fields, "Setup guide", self.show_guide)
        self.g_guide_btn.grid(row=2, column=0, columnspan=2, sticky="w", pady=(self.px(8), 0))
        tk.Label(self.g_adv, text="Or skip signing in: each new event opens in your browser and you press Save there (one extra click per event).",
                 bg=CARD, fg=DIM, font=(theme.FONT, 10), justify="left", anchor="w", wraplength=self.px(560)).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(self.px(14), 0))
        self.g_use_btn = self._button(self.g_adv, "Open events in my browser", self.google_use_link)
        self.g_use_btn.grid(row=3, column=0, columnspan=3, sticky="w", pady=(self.px(6), 0))

    def toggle_advanced(self):
        self.g_adv_touched = True
        self.g_adv_open = not self.g_adv_open
        self._apply_advanced()

    def _apply_advanced(self):
        if self.g_adv_open:
            self.g_adv.grid()
        else:
            self.g_adv.grid_remove()
        self.g_adv_btn.config(text=("\u25be" if self.g_adv_open else "\u25b8") + "  Advanced options")

    def toggle_own(self):
        self.g_own_touched = True
        self.g_own_var.set(not self.g_own_var.get())
        self._apply_own_mode()

    def _apply_own_mode(self):
        """Show the client ID/secret fields only when the user's own Google project is in use."""
        import google_cal
        has_builtin = google_cal.bundled_client() is not None
        own = self.g_own_var.get() or not has_builtin
        if own:
            self.g_fields.grid()
        else:
            self.g_fields.grid_remove()
        if has_builtin:
            self.g_own_btn.config(text="\u25be  Use the built-in sign-in instead" if own else "\u25b8  Use my own Google project")
        else:
            self.g_own_btn.config(text="This copy has no built-in Google sign-in. Use your own Google project (Setup guide), or open events in your browser.")

    def google_use_link(self):
        settings.update({"google": {"mode": "link"}})
        self.say(self.google_msg, "Google Calendar is ready. New events open in your browser; press Save there.", "ok")
        self.reload()

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
        """A designed, scrollable checklist of the Google setup steps (google_cal.GUIDE_STEPS)."""
        import google_cal
        box = tk.Toplevel(self.top)
        self.guide_box = box
        box.title("Google setup guide")
        box.configure(bg=BG)
        branding.set_window_icon(box)
        width = self.px(700)
        box.geometry("%dx%d" % (width, min(self.px(780), int(box.winfo_screenheight() * 0.9))))
        box.minsize(self.px(520), self.px(420))
        box.bind("<Escape>", lambda e: box.destroy())
        try:
            box.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(box.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

        top_row = tk.Frame(box, bg=BG)
        top_row.pack(fill="x", padx=self.px(28), pady=(self.px(22), self.px(8)))
        self.guide_mascot = branding.photo("tile", self.px(64), master=box)
        if self.guide_mascot is not None:
            tk.Label(top_row, image=self.guide_mascot, bg=BG).pack(side="left", padx=(0, self.px(16)), anchor="n")
        head = tk.Frame(top_row, bg=BG)
        head.pack(side="left", fill="x", expand=True)
        tk.Label(head, text="Set up Google sign-in", bg=BG, fg=FG, font=(theme.FONT, 20, "bold")).pack(anchor="w")
        tk.Label(head, text=google_cal.GUIDE_TIME, bg=BG, fg=ACCENT, font=(theme.FONT, 11)).pack(anchor="w", pady=(self.px(4), 0))
        tk.Label(head, text="Only the person who ships Bamboo does this, once. Everyone else just presses \u201cSign in with Google\u201d.",
                 bg=BG, fg=DIM, font=(theme.FONT, 10), wraplength=width - self.px(170), justify="left").pack(anchor="w", pady=(self.px(6), 0))

        foot = tk.Frame(box, bg=BG)
        foot.pack(side="bottom", fill="x", padx=self.px(28), pady=self.px(14))
        tk.Button(foot, text="Close", command=box.destroy, bg=FIELD, fg=FG, relief="flat", bd=0, padx=self.px(20), pady=self.px(6),
                  font=(theme.FONT, 10), cursor="hand2").pack(side="right")

        canvas = tk.Canvas(box, bg=BG, highlightthickness=0)
        canvas.pack(side="top", fill="both", expand=True, padx=(self.px(28), self.px(12)))
        body = tk.Frame(canvas, bg=BG)
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        box.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        self.guide_open_buttons = []
        for number, (title, detail, url) in enumerate(google_cal.GUIDE_STEPS, 1):
            card = tk.Frame(body, bg=CARD)
            card.pack(fill="x", pady=(0, self.px(10)))
            tk.Frame(card, bg=ACCENT_FILL, width=self.px(4)).pack(side="left", fill="y")
            badge = tk.Label(card, text=str(number), bg=ACCENT_FILL, fg=ON_FILL, font=(theme.FONT, 13, "bold"), width=2)
            badge.pack(side="left", anchor="n", padx=(self.px(16), self.px(14)), pady=self.px(16), ipady=self.px(3))
            text = tk.Frame(card, bg=CARD)
            text.pack(side="left", fill="x", expand=True, pady=self.px(14), padx=(0, self.px(16)))
            tk.Label(text, text=title, bg=CARD, fg=FG, font=(theme.FONT, 13, "bold"), anchor="w").pack(fill="x")
            tk.Label(text, text=detail, bg=CARD, fg=DIM, font=(theme.FONT, 10), justify="left", anchor="w",
                     wraplength=width - self.px(190)).pack(fill="x", pady=(self.px(4), 0))
            if url:
                button = tk.Button(text, text="Open this page \u2197", command=lambda u=url: webbrowser.open(u), bg=FIELD, fg=ACCENT,
                                   activebackground=FIELD, activeforeground=FG, relief="flat", bd=0, padx=self.px(12), pady=self.px(4),
                                   font=(theme.FONT, 10), cursor="hand2")
                button.pack(anchor="w", pady=(self.px(8), 0))
                self.guide_open_buttons.append((url, button))
        return box

    def google_sign_in(self):
        import google_cal
        if self.cancel is not None and not self.cancel.is_set():          # the button doubles as Cancel while waiting
            self.cancel.set()
            return
        builtin = google_cal.bundled_client()
        own = self.g_own_var.get() or builtin is None
        if own:
            client_id, secret = self.g_id_var.get().strip(), self.g_secret_var.get().strip()
            if not client_id or not secret:
                self.say(self.google_msg, "Paste the client ID and client secret from your Google Cloud project first (see Setup guide).", "error")
                return
        else:
            client_id, secret = builtin
        if not self.ensure_unlocked(self.google_msg):
            self.say(self.google_msg, "Not started: the passphrase is needed to store the sign-in.", "error")
            return
        self.cancel = threading.Event()
        self.g_signin_btn.config(text="Cancel")
        self.say(self.google_msg, "Your browser opens next. Choose your Google account and press Allow." if not own
                 else "Waiting for you in the browser…")
        cancel = self.cancel
        self.run_async(lambda: google_cal.sign_in(client_id, secret, cancel=cancel),
                       lambda tokens, error: self._google_signed_in(client_id, secret, own, tokens, error))

    def _google_signed_in(self, client_id, secret, own, tokens, error):
        import google_cal
        self.cancel = None
        self.g_signin_btn.config(text="Sign in with Google")
        if error:
            self.say(self.google_msg, str(error), "error")
            return
        try:
            google_cal.save_connection(client_id, secret, tokens["refresh_token"], tokens.get("access_token"),
                                       tokens.get("expires_in", 0), own_client=own)
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
        if not settings.load()["google"].get("client_id"):                # browser-link mode only: nothing stored, nothing to revoke
            cfg = settings.load()
            cfg["google"] = {}
            if cfg.get("default_target") == "google":
                cfg["default_target"] = None
            settings.save(cfg)
            self.say(self.google_msg, "Google Calendar disconnected.", "ok")
            self.reload()
            return
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
        import bamboo
        bamboo.apply_settings()
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
        tk.Checkbutton(page, text="Start Bamboo when I log in to Windows", variable=self.autostart_var, command=self.toggle_autostart,
                       bg=CARD, fg=FG, selectcolor=FIELD, activebackground=CARD, activeforeground=FG, font=(theme.FONT, 10)).grid(
            row=5, column=0, columnspan=3, sticky="w")
        self.playful_var = tk.BooleanVar()
        tk.Checkbutton(page, text="Friendly messages from Bamboo", variable=self.playful_var, command=self.toggle_playful,
                       bg=CARD, fg=FG, selectcolor=FIELD, activebackground=CARD, activeforeground=FG, font=(theme.FONT, 10)).grid(
            row=14, column=0, columnspan=3, sticky="w", pady=(self.px(10), 0))
        self._heading(page, 6, "Maintenance")
        self.lock_btn = self._button(page, "Lock now", self.lock_now)
        self.log_btn = self._button(page, "Open log", self.open_log)
        self.restart_btn = self._button(page, "Restart Bamboo", self.restart_app)
        self._row(page, 7, self.lock_btn, self.log_btn, self.restart_btn)
        self.general_msg = self._status(page, 8)

    def save_hotkey(self):
        text = self.hotkey_var.get().strip()
        if not settings.valid_hotkey(text):
            self.say(self.hotkey_msg, "Not a valid hotkey. Examples: ctrl+alt+C, ctrl+shift+7, alt+F9. It needs Ctrl, Alt or Win.", "error")
            return
        settings.update({"defaults": {"hotkey": text}})
        self.say(self.hotkey_msg, "Saved. Press Restart Bamboo for the new hotkey to take effect.", "ok")

    def toggle_autostart(self):
        from bamboo_ctl import set_autostart
        try:
            set_autostart(self.autostart_var.get())
            self.say(self.general_msg, "Will start with Windows." if self.autostart_var.get() else "Will not start with Windows.", "ok")
        except Exception as err:  # noqa: BLE001
            self.say(self.general_msg, "Could not change it: %s" % err, "error")

    def toggle_playful(self):
        settings.update({"defaults": {"playful": bool(self.playful_var.get())}})
        self.say(self.general_msg, "Bamboo will talk to you." if self.playful_var.get() else "Bamboo will keep to plain wording.", "ok")

    def lock_now(self):
        vault.lock()
        self.say(self.general_msg, "Locked. The passphrase is needed for the next event.", "ok")

    def open_log(self):
        import bamboo
        try:
            os.startfile(bamboo.LOG)
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
        import google_cal
        g = cfg["google"]
        mode = settings.google_mode(cfg)
        has_builtin = google_cal.bundled_client() is not None
        if mode == "link":
            self.say(self.google_state, "Connected: new events open in your browser (one extra click per event).", "ok")
        elif settings.google_configured(cfg):
            self.say(self.google_state, "Connected: %s" % (g.get("calendar_name") or g["calendar_id"]), "ok")
            self.g_cal.config(values=[g.get("calendar_name") or g["calendar_id"]])
            self.g_cal_var.set(g.get("calendar_name") or g["calendar_id"])
        elif g.get("client_id"):
            self.say(self.google_state, "Signed in, but no calendar chosen yet.", "warn")
        elif has_builtin:
            self.say(self.google_state, "Not connected. Press \u201cSign in with Google\u201d.", "info")
        else:
            self.say(self.google_state, "Not connected. This copy has no built-in Google sign-in: open Advanced options.", "warn")
        if g.get("own_client") and g.get("client_id"):
            self.g_id_var.set(g["client_id"])
        if not self.g_own_touched:
            self.g_own_var.set(bool(g.get("own_client")))
        if not self.g_adv_touched:
            self.g_adv_open = mode == "api" and bool(g.get("own_client")) or not has_builtin
        self._apply_own_mode()
        self._apply_advanced()
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
        self.playful_var.set(bool(d.get("playful", True)))
        try:
            from bamboo_ctl import autostart_enabled
            self.autostart_var.set(autostart_enabled())
        except Exception:  # noqa: BLE001
            pass

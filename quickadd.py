"""Quick-add calendar popup (Windows). Press the hotkey, type "dentist friday 3pm", Enter.

    pythonw quickadd.py        # no console window; python quickadd.py to see errors
    qa start | stop | restart  # see quickadd_ctl.py

Standard library for the window (tkinter) and the global hotkey (RegisterHotKey via ctypes). Parsing is
eventparser.py, calendar files are eventout.py, calendars are calendars.py, secrets are vault.py.
"""
import atexit
import ctypes
import datetime as dt
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from pathlib import Path

import calendars
import commands
import eventparser
import settings
import singleton
import theme
import vault
from eventout import OUT_DIR, build_ics, write_ics                      # noqa: F401 - re-exported for scripts/tests
from eventout import local_zone as _local_zone                           # noqa: F401
from eventparser import parse   # ctparse is imported lazily inside, on the first keystroke

from theme import ACCENT, BG, BORDER, CARD, DIM, ERR, FG, OK, WARN  # noqa: E402 - colours shared with the settings window

FONT = theme.FONT       # upgraded to "Segoe UI Variable Text" at startup when installed
ICON_FONT = "Segoe MDL2 Assets"
CALENDAR_GLYPH = ""


def _day(value):
    return value.date() if isinstance(value, dt.datetime) else value


def _length(minutes):
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    return " ".join(p for p in ("%d d" % days if days else "", "%d h" % hours if hours else "",
                                "%d min" % mins if mins else "") if p) or "0 min"


def date_text(ev):
    first, last = _day(ev["start"]), _day(ev["end"])
    if first == last:
        return "%s, %d %s" % (first.strftime("%A"), first.day, first.strftime("%B"))
    return "%s %d %s  →  %s %d %s" % (first.strftime("%a"), first.day, first.strftime("%b"),
                                          last.strftime("%a"), last.day, last.strftime("%b"))


def time_text(ev):
    if ev["all_day"]:
        days = (_day(ev["end"]) - _day(ev["start"])).days + 1
        return "All day" if days == 1 else "All day  ·  %d days" % days
    if _day(ev["start"]) == _day(ev["end"]):
        return "%s – %s  ·  %s" % (ev["start"].strftime("%H:%M"), ev["end"].strftime("%H:%M"), _length(ev["minutes"]))
    return "%s  →  %s  ·  %s" % (ev["start"].strftime("%H:%M"), ev["end"].strftime("%H:%M"), _length(ev["minutes"]))


def repeat_text(ev):
    rule = ev.get("rrule") or ""
    if not rule:
        return ""
    if "BYDAY=MO,TU,WE,TH,FR" in rule:
        return "Every weekday"
    if "BYDAY=" in rule:
        day = {"MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday", "FR": "Friday",
               "SA": "Saturday", "SU": "Sunday"}[rule.split("BYDAY=")[1]]
        return "Every " + day
    return {"FREQ=DAILY": "Every day", "FREQ=WEEKLY": "Every week", "FREQ=MONTHLY": "Every month"}.get(rule, rule)


def save_event(ev, target, out):
    """Worker thread: sends the event to `target`, falls back to a local .ics, reports on `out`."""
    try:
        cal = calendars.add_event(target, ev, build_ics(ev))
        if calendars.is_manual(target):               # only opened in the browser: the user still presses Save there
            out.put(("opened", "Opened in %s" % calendars.label(target), "Press Save in your browser  ·  %s" % ev["title"]))
        else:
            out.put(("saved", "Added to %s" % calendars.label(target), "%s  ·  %s" % (cal, ev["title"])))
    except Exception as err:  # noqa: BLE001 - any failure must still end in a saved file
        try:
            path = write_ics(ev)
            out.put(("fallback", "%s failed, saved a file instead" % calendars.label(target),
                     "%s  (%s)" % (path.name, err)))
        except OSError as os_err:
            out.put(("error", "Could not save", str(os_err)))


def apply_settings():
    """Push the saved settings into the modules that read them. Cheap, so it runs on every show()."""
    d = settings.load()["defaults"]
    eventparser.DEFAULT_MINUTES = d["duration_min"]
    vault.UNLOCK_IDLE_SECONDS = d["unlock_idle_min"] * 60


# ---------------------------------------------------------------------------- hotkey (Win32)

def hotkey_label(text=None):
    mods, key = settings.parse_hotkey(text or settings.load()["defaults"]["hotkey"])
    return "+".join([m.capitalize() for m in mods] + [key])


def listen_for_hotkey(out):
    """Blocks in a message loop on its own thread; puts "hotkey" on `out` each press."""
    mods, key = settings.parse_hotkey(settings.load()["defaults"]["hotkey"])
    flags = 0x4000  # MOD_NOREPEAT
    for part in mods:
        flags |= {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}[part]
    vk = 0x6F + int(key[1:]) if key.startswith("F") and len(key) > 1 else ord(key)
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, flags, vk):
        out.put("hotkey-failed")
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            out.put("hotkey")


# ---------------------------------------------------------------------------- window

class Popup:
    """`service=True` is the real, installed behaviour (tray icon, quit-on-request); tests leave it off."""
    BASE_WIDTH = 680    # design pixels at 100% scaling; multiplied by the screen's DPI scale
    BASE_SIDE = 28

    def __init__(self, service=False):
        self.service = service
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        global FONT
        self.root = r = tk.Tk()
        r.withdraw()
        self.k = r.winfo_fpixels("1i") / 96.0           # DPI scale: 1.0 at 100%, 1.5 at 150%, ...
        self.WIDTH, self.SIDE = self.px(self.BASE_WIDTH), self.px(self.BASE_SIDE)
        families = set(tkfont.families(r))
        if "Segoe UI Variable Text" in families:
            FONT = theme.FONT = "Segoe UI Variable Text"
        has_icons = ICON_FONT in families
        self.busy = False
        self.unlocking, self.pending, self.pending_text, self.pending_target = False, None, "", None
        apply_settings()
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.configure(bg=BORDER)   # the 1px frame colour shows through around `outer`

        outer = tk.Frame(r, bg=BG)
        outer.pack(fill="both", expand=True, padx=1, pady=1)

        # input row
        row = tk.Frame(outer, bg=BG)
        row.pack(fill="x", padx=self.SIDE, pady=(self.px(24), self.px(20)))
        if has_icons:
            tk.Label(row, text=CALENDAR_GLYPH, font=(ICON_FONT, 17), bg=BG, fg=ACCENT).pack(side="left", padx=(0, self.px(14)))
        self.var = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.var, font=(FONT, 18), bg=BG, fg=FG,
                              insertbackground=ACCENT, insertwidth=2, relief="flat", highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, ipady=self.px(4))
        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x")

        # preview card
        body = tk.Frame(outer, bg=BG)
        body.pack(fill="x", padx=self.SIDE, pady=(self.px(20), self.px(6)))
        self.card = tk.Frame(body, bg=CARD)
        self.card.pack(fill="x")
        self.bar = tk.Frame(self.card, bg=DIM, width=self.px(4))
        self.bar.pack(side="left", fill="y")
        inner = tk.Frame(self.card, bg=CARD)
        inner.pack(side="left", fill="x", expand=True, padx=self.px(20), pady=self.px(16))
        self.title_lbl = tk.Label(inner, font=(FONT, 15, "bold"), bg=CARD, fg=FG, anchor="w", justify="left",
                                  wraplength=self.WIDTH - 2 * self.SIDE - self.px(70))
        self.title_lbl.pack(fill="x")
        meta = tk.Frame(inner, bg=CARD)
        meta.pack(fill="x", pady=(self.px(6), 0))
        self.date_lbl = tk.Label(meta, font=(FONT, 11), bg=CARD, fg=FG, anchor="w", justify="left",
                                 wraplength=self.WIDTH - 2 * self.SIDE - self.px(90))
        self.date_lbl.pack(side="left")
        self.time_lbl = tk.Label(meta, font=(FONT, 11), bg=CARD, fg=ACCENT, anchor="w")
        self.time_lbl.pack(side="left", padx=(self.px(18), 0))
        self.note_lbl = tk.Label(inner, font=(FONT, 10), bg=CARD, fg=DIM, anchor="w")
        self.note_lbl.pack(fill="x", pady=(self.px(6), 0))

        # footer with key caps
        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=self.SIDE, pady=(self.px(10), self.px(18)))
        self.foot_lbl = tk.Label(foot, text="", font=(FONT, 9), bg=BG, fg=DIM)
        self.foot_lbl.pack(side="left")
        for key, label in (("Esc", "Close"), ("↵", "Add")):
            tk.Label(foot, text=label, font=(FONT, 9), bg=BG, fg=DIM).pack(side="right", padx=(self.px(8), 0))
            tk.Label(foot, text=key, font=(FONT, 9, "bold"), bg=CARD, fg=FG, padx=self.px(7), pady=self.px(1)).pack(side="right", padx=(self.px(14), 0))

        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Escape>", self.on_escape)
        self.entry.bind("<Tab>", self.on_tab)
        self.var.trace_add("write", lambda *a: self.refresh())
        r.bind("<FocusOut>", self.on_focus_out)

        self.events = queue.Queue()
        threading.Thread(target=listen_for_hotkey, args=(self.events,), daemon=True).start()
        self.tray = None
        if service:
            import tray
            from quickadd_ctl import autostart_enabled, set_autostart
            self.stop_event = singleton.create_stop_event()
            self.tray = tray.start(self.events, hotkey_label(), autostart_enabled,
                                   lambda: set_autostart(not autostart_enabled()))
        r.after(150, self.poll)

    def px(self, n):
        return int(round(n * self.k))

    def poll(self):
        if self.service and singleton.stop_requested(self.stop_event):
            self.quit()
            return
        try:
            while True:
                item = self.events.get_nowait()
                if item in ("hotkey", "tray-add"):
                    self.toggle() if item == "hotkey" else self.show()
                elif item == "tray-quit":
                    self.quit()
                    return
                elif item == "tray-restart":
                    self.restart()
                elif item == "tray-settings":
                    self.open_settings()
                elif item == "hotkey-failed":
                    print("Could not register hotkey (already in use?).")
                elif isinstance(item, tuple) and item[0] == "unlock":
                    self.on_unlock_result(item[1])
                elif isinstance(item, tuple):
                    self.on_saved(*item)
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def toggle(self):
        if self.root.state() == "withdrawn":
            self.show()
        else:
            self.hide()

    def show(self):
        r = self.root
        apply_settings()
        self.leave_unlock_mode()
        self.var.set("")
        self.update_footer()
        self.refresh()
        r.update_idletasks()
        w, h = self.WIDTH, r.winfo_reqheight()
        x = (r.winfo_screenwidth() - w) // 2
        y = int(r.winfo_screenheight() * 0.22)
        r.geometry("%dx%d+%d+%d" % (w, h, x, y))
        r.deiconify()
        self.round_corners()
        r.lift()
        r.focus_force()
        self.entry.focus_set()

    def hide(self):
        self.leave_unlock_mode()
        self.root.withdraw()

    def on_focus_out(self, _event):
        # Fires when focus moves between our own widgets too; only hide if it truly left the window.
        self.root.after(120, lambda: self.root.focus_displayof() is None and self.hide())

    def round_corners(self):
        try:  # Windows 11 only; silently skipped elsewhere
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    def set_card(self, bar, title, title_fg, date="", time="", time_fg=ACCENT, note="", note_fg=DIM):
        self.bar.config(bg=bar)
        self.title_lbl.config(text=title, fg=title_fg)
        self.date_lbl.config(text=date)
        self.time_lbl.config(text=time, fg=time_fg)
        self.note_lbl.config(text=note, fg=note_fg)
        self.fit()

    def fit(self):
        """Grow or shrink the window to its content, keeping its position (a card can change height)."""
        if self.root.state() == "withdrawn":
            return
        self.root.update_idletasks()
        height = self.root.winfo_reqheight()
        if height != self.root.winfo_height():
            self.root.geometry("%dx%d" % (self.WIDTH, height))

    def update_footer(self, override=None):
        target = calendars.choose_target(override=override)
        self.foot_lbl.config(text=calendars.footer_label(target) if target else "Saved to Documents\\quickadd")

    def refresh(self):
        if self.unlocking:
            return                                    # the entry holds a passphrase now, not an event
        text = self.var.get()
        if commands.is_command(text):
            title, detail = commands.suggestions(text)
            self.set_card(ACCENT, title, FG, detail, "")
            self.date_lbl.config(fg=DIM)
            return
        clean, override = commands.split_override(text)
        self.update_footer(override)
        ev = parse(clean)
        if ev is None:
            self.set_card(DIM, "Describe your event", DIM,
                          "dentist friday 3pm   ·   lunch with Sam tomorrow 12:30   ·   /help for commands", "")
            self.date_lbl.config(fg=DIM)
        else:
            self.date_lbl.config(fg=FG)
            parts = ([repeat_text(ev)] if ev["rrule"] else []) + ev["hints"]
            if override:
                chosen = calendars.choose_target(override=override)
                parts.append("→ %s (this event only)" % calendars.label(chosen) if chosen == override
                             else "%s is not set up, using %s" % (calendars.label(override), calendars.label(chosen) if chosen else "a file"))
            self.set_card(WARN if ev["all_day"] else ACCENT, ev["title"], FG,
                          date_text(ev), time_text(ev), WARN if ev["all_day"] else ACCENT,
                          "  ·  ".join(parts), ACCENT if ev["rrule"] and not ev["hints"] and not override else WARN)

    # ---- slash commands

    def on_tab(self, _event):
        text = self.var.get()
        if commands.is_command(text) and not self.unlocking:
            self.var.set(commands.complete(text))
            self.entry.icursor("end")
        return "break"                                 # keep Tab from moving focus out of the entry

    def run_command(self, text):
        result = commands.run(text)
        color = {"ok": OK, "error": ERR, "info": ACCENT}[result.kind]
        self.set_card(color, result.title, FG if result.kind == "info" else color, result.detail, "")
        self.date_lbl.config(fg=DIM)
        if result.action == "settings":
            self.open_settings()
        elif result.action == "log":
            try:
                os.startfile(LOG)
            except OSError:
                self.set_card(ERR, "No log yet", ERR, str(LOG), "")
        elif result.action == "restart":
            self.restart()
        elif result.action == "quit":
            self.root.after(300, self.quit)
        if result.autohide and result.action not in ("quit",):
            self.root.after(900, self.hide)

    def open_settings(self):
        try:
            import settings_window
            settings_window.open_window(self)
        except ImportError:
            self.set_card(ERR, "Settings window is not available", ERR, "settings_window.py is missing.", "")

    # ---- passphrase prompt: secrets are encrypted on disk and unlocked on demand

    def enter_unlock_mode(self, ev, typed, target):
        self.unlocking, self.pending, self.pending_text, self.pending_target = True, ev, typed, target
        self.var.set("")
        self.entry.config(show="•")
        self.set_card(WARN, "Unlock %s" % calendars.label(target), FG, "Type your passphrase and press Enter", "")
        self.date_lbl.config(fg=DIM)

    def leave_unlock_mode(self):
        self.unlocking, self.pending = False, None
        self.entry.config(show="")

    def cancel_unlock(self):
        typed = self.pending_text
        self.leave_unlock_mode()
        self.var.set(typed)                            # back to the event they were typing
        self.refresh()

    def check_passphrase(self):
        passphrase = self.var.get()
        self.var.set("")
        self.busy = True
        self.set_card(ACCENT, "Unlocking…", DIM, "", "")

        def work():
            self.events.put(("unlock", vault.unlock(passphrase)))
        threading.Thread(target=work, daemon=True).start()

    def on_unlock_result(self, ok):
        self.busy = False
        if not ok:
            self.set_card(ERR, "Wrong passphrase", ERR, "Try again, or press Esc to go back", "")
            self.date_lbl.config(fg=DIM)
            return
        ev, target = self.pending, self.pending_target
        self.leave_unlock_mode()
        self.start_save(ev, target)

    def start_save(self, ev, target):
        self.busy = True
        self.set_card(ACCENT, ("Opening %s…" if calendars.is_manual(target) else "Adding to %s…") % calendars.label(target),
                      DIM, ev["title"], "")
        self.date_lbl.config(fg=DIM)
        threading.Thread(target=save_event, args=(ev, target, self.events), daemon=True).start()

    def on_escape(self, _event):
        if self.unlocking and not self.busy:
            self.cancel_unlock()
        else:
            self.hide()

    def on_enter(self, _event):
        if self.busy:
            return
        if self.unlocking:
            self.check_passphrase()
            return
        text = self.var.get()
        if commands.is_command(text):
            self.run_command(text)
            return
        clean, override = commands.split_override(text)
        ev = parse(clean)
        if ev is None:
            return
        target = calendars.choose_target(override=override)
        if target is not None:
            if calendars.needs_unlock(target):
                self.enter_unlock_mode(ev, text, target)
            else:
                self.start_save(ev, target)
            return
        try:
            path = write_ics(ev)
        except OSError as err:
            self.set_card(ERR, "Could not save", ERR, str(err), "")
            return
        self.on_saved("saved", "Saved as a calendar file", path.name)

    def on_saved(self, kind, title, detail):
        self.busy = False
        color = {"saved": OK, "opened": OK, "fallback": WARN, "error": ERR}[kind]
        self.set_card(color, title, color, detail, "")
        self.date_lbl.config(fg=DIM)
        if kind != "error":
            self.root.after(1400 if kind in ("fallback", "opened") else 1000, self.hide)

    def quit(self):
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def restart(self):
        """Hand over to the control command, which stops this process and starts a fresh one (new code)."""
        ctl = Path(__file__).resolve().with_name("quickadd_ctl.py")
        subprocess.Popen([sys.executable, str(ctl), "restart"], creationflags=0x08000000 | 0x00000008,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def run(self):
        self.root.mainloop()


LOG = Path.home() / ".quickadd.log"
PIDFILE = Path.home() / ".quickadd.pid"


def _log_to_file():
    """Under pythonw there is no console, so errors would vanish: keep them in ~/.quickadd.log."""
    if ctypes.windll.kernel32.GetConsoleWindow():      # started from a terminal: keep output there
        return
    try:
        if LOG.exists() and LOG.stat().st_size > 1_000_000:
            LOG.unlink()
        sys.stdout = sys.stderr = open(LOG, "a", encoding="utf-8", buffering=1)
        print("---- %s  started, pid %d" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    except OSError:
        pass


def main():
    _log_to_file()
    if not singleton.acquire():
        print("already running, nothing to do")
        return
    PIDFILE.write_text(str(os.getpid()))
    atexit.register(lambda: PIDFILE.unlink(missing_ok=True))
    Popup(service=True).run()


if __name__ == "__main__":
    main()

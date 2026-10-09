"""Quick-add calendar popup (Windows). Press the hotkey, type "dentist friday 3pm", Enter.

    pythonw quickadd.py        # no console window; python quickadd.py to see errors

Standard library only: tkinter for the window, RegisterHotKey (ctypes) for the global hotkey.
The parser below is a PLACEHOLDER (day words + simple times). Replace `parse` with ctparse /
GLiNER later; the UI only needs it to return a dict like the one documented there.
"""
import atexit
import ctypes
import datetime as dt
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import uuid
from ctypes import wintypes
from pathlib import Path

import singleton

HOTKEY = ("ctrl+alt", "C")   # Ctrl+Alt+C
OUT_DIR = Path.home() / "Documents" / "quickadd"

BORDER, BG, CARD = "#2e3140", "#17181d", "#1f2129"
FG, DIM, ACCENT, WARN, OK, ERR = "#eceef4", "#868b9b", "#7c9cff", "#f0b35a", "#5fd39a", "#ef6b6b"
FONT = "Segoe UI"       # upgraded to "Segoe UI Variable Text" at startup when installed
ICON_FONT = "Segoe MDL2 Assets"
CALENDAR_GLYPH = ""


from eventparser import parse   # ctparse is imported lazily inside, on the first keystroke


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


def _local_zone():
    """(IANA name, ZoneInfo) of the PC's timezone right now, or None if it cannot be worked out."""
    try:
        import tzlocal
        from zoneinfo import ZoneInfo
        name = tzlocal.get_localzone_name()
        return name, ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure just means "fall back to UTC"
        return None


def _vtimezone(name, zone, ev):
    """A VTIMEZONE block covering the event (and, for repeating events, many years after it)."""
    import icalendar
    last_year = ev["end"].year + (10 if ev.get("rrule") else 1)
    vtz = icalendar.Timezone.from_tzinfo(zone, tzid=name, first_date=dt.date(ev["start"].year - 1, 1, 1),
                                         last_date=dt.date(last_year, 12, 31))
    return vtz.to_ical().decode("utf-8").strip().split("\r\n")


def build_ics(ev):
    """Timed events carry the PC's timezone (TZID + VTIMEZONE), so 9:30 is 9:30 where you are, and a
    weekly event keeps its clock time across daylight saving. Without a timezone calendars guess
    (Notion Calendar read the bare time as UTC and showed 9:30 as 11:30). If the zone cannot be
    found the times are written in UTC instead, which is still the right moment."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zone_lines = []
    if ev["all_day"]:
        # an all-day DTEND is exclusive, so the last day shown to the user is one day before it
        when = "DTSTART;VALUE=DATE:%s\r\nDTEND;VALUE=DATE:%s" % (
            ev["start"].strftime("%Y%m%d"), (ev["end"] + dt.timedelta(days=1)).strftime("%Y%m%d"))
    else:
        found = _local_zone()
        fmt = "%Y%m%dT%H%M%S"
        if found:
            name, zone = found
            zone_lines = _vtimezone(name, zone, ev)
            when = "DTSTART;TZID=%s:%s\r\nDTEND;TZID=%s:%s" % (name, ev["start"].strftime(fmt), name, ev["end"].strftime(fmt))
        else:
            as_utc = lambda value: value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731 - naive = PC local time
            when = "DTSTART:%s\r\nDTEND:%s" % (as_utc(ev["start"]), as_utc(ev["end"]))
    title = ev["title"].replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    lines = (["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//quickadd//EN"] + zone_lines
             + ["BEGIN:VEVENT", "UID:%s@quickadd" % uuid.uuid4(), "DTSTAMP:" + stamp, when, "SUMMARY:" + title])
    if ev.get("rrule"):
        lines.append("RRULE:" + ev["rrule"])
    return "\r\n".join(lines + ["END:VEVENT", "END:VCALENDAR", ""])


def write_ics(ev):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w\- ]", "", ev["title"])[:40].strip() or "event"
    path = OUT_DIR / ("%s_%s.ics" % (dt.datetime.now().strftime("%Y%m%d-%H%M%S"), name))
    path.write_text(build_ics(ev), encoding="utf-8", newline="")
    return path


def save_to_icloud(ev, out):
    """Worker thread: sends the event to iCloud, falls back to a local .ics, reports on `out`."""
    try:
        import icloud
        cal = icloud.add_event(build_ics(ev))
        out.put(("saved", "Added to Apple Calendar", "%s  ·  %s" % (cal, ev["title"])))
    except Exception as err:  # noqa: BLE001 - any failure must still end in a saved file
        try:
            path = write_ics(ev)
            out.put(("fallback", "iCloud failed, saved a file instead", "%s  (%s)" % (path.name, err)))
        except OSError as os_err:
            out.put(("error", "Could not save", str(os_err)))


# ---------------------------------------------------------------------------- hotkey (Win32)

def listen_for_hotkey(out):
    """Blocks in a message loop on its own thread; puts "hotkey" on `out` each press."""
    mods = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
    flags = 0x4000  # MOD_NOREPEAT
    for part in HOTKEY[0].split("+"):
        flags |= mods[part]
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, flags, ord(HOTKEY[1])):
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
            FONT = "Segoe UI Variable Text"
        has_icons = ICON_FONT in families
        self.busy = False
        self.unlocking, self.pending, self.pending_text = False, None, ""
        try:
            import icloud
            self.use_icloud = icloud.is_configured()   # cheap: reads a small JSON file only
        except Exception:
            self.use_icloud = False
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
        self.date_lbl = tk.Label(meta, font=(FONT, 11), bg=CARD, fg=FG, anchor="w")
        self.date_lbl.pack(side="left")
        self.time_lbl = tk.Label(meta, font=(FONT, 11), bg=CARD, fg=ACCENT, anchor="w")
        self.time_lbl.pack(side="left", padx=(self.px(18), 0))
        self.note_lbl = tk.Label(inner, font=(FONT, 10), bg=CARD, fg=DIM, anchor="w")
        self.note_lbl.pack(fill="x", pady=(self.px(6), 0))

        # footer with key caps
        foot = tk.Frame(outer, bg=BG)
        foot.pack(fill="x", padx=self.SIDE, pady=(self.px(10), self.px(18)))
        tk.Label(foot, text="Apple Calendar" if self.use_icloud else "Saved to Documents\\quickadd",
                 font=(FONT, 9), bg=BG, fg=DIM).pack(side="left")
        for key, label in (("Esc", "Close"), ("↵", "Add")):
            tk.Label(foot, text=label, font=(FONT, 9), bg=BG, fg=DIM).pack(side="right", padx=(self.px(8), 0))
            tk.Label(foot, text=key, font=(FONT, 9, "bold"), bg=CARD, fg=FG, padx=self.px(7), pady=self.px(1)).pack(side="right", padx=(self.px(14), 0))

        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Escape>", self.on_escape)
        self.var.trace_add("write", lambda *a: self.refresh())
        r.bind("<FocusOut>", self.on_focus_out)

        self.events = queue.Queue()
        threading.Thread(target=listen_for_hotkey, args=(self.events,), daemon=True).start()
        self.tray = None
        if service:
            import tray
            from quickadd_ctl import autostart_enabled, set_autostart
            self.stop_event = singleton.create_stop_event()
            self.tray = tray.start(self.events, "Ctrl+Alt+C", autostart_enabled,
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
        self.leave_unlock_mode()
        self.var.set("")
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

    def refresh(self):
        if self.unlocking:
            return                                    # the entry holds a passphrase now, not an event
        ev = parse(self.var.get())
        if ev is None:
            self.set_card(DIM, "Describe your event", DIM,
                          "dentist friday 3pm   ·   lunch with Sam tomorrow 12:30", "")
            self.date_lbl.config(fg=DIM)
        else:
            self.date_lbl.config(fg=FG)
            parts = ([repeat_text(ev)] if ev["rrule"] else []) + ev["hints"]
            self.set_card(WARN if ev["all_day"] else ACCENT, ev["title"], FG,
                          date_text(ev), time_text(ev), WARN if ev["all_day"] else ACCENT,
                          "  ·  ".join(parts), ACCENT if ev["rrule"] and not ev["hints"] else WARN)

    # ---- passphrase prompt: the iCloud password is encrypted on disk and unlocked on demand

    def enter_unlock_mode(self, ev, typed):
        self.unlocking, self.pending, self.pending_text = True, ev, typed
        self.var.set("")
        self.entry.config(show="•")
        self.set_card(WARN, "Unlock Apple Calendar", FG, "Type your passphrase and press Enter", "")
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
            import icloud
            self.events.put(("unlock", icloud.unlock(passphrase)))
        threading.Thread(target=work, daemon=True).start()

    def on_unlock_result(self, ok):
        self.busy = False
        if not ok:
            self.set_card(ERR, "Wrong passphrase", ERR, "Try again, or press Esc to go back", "")
            self.date_lbl.config(fg=DIM)
            return
        ev = self.pending
        self.leave_unlock_mode()
        self.start_icloud_save(ev)

    def start_icloud_save(self, ev):
        self.busy = True
        self.set_card(ACCENT, "Adding to Apple Calendar…", DIM, ev["title"], "")
        self.date_lbl.config(fg=DIM)
        threading.Thread(target=save_to_icloud, args=(ev, self.events), daemon=True).start()

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
        ev = parse(self.var.get())
        if ev is None:
            return
        if self.use_icloud:
            import icloud
            if icloud.needs_unlock():
                self.enter_unlock_mode(ev, self.var.get())
            else:
                self.start_icloud_save(ev)
            return
        try:
            path = write_ics(ev)
        except OSError as err:
            self.set_card(ERR, "Could not save", ERR, str(err), "")
            return
        self.on_saved("saved", "Saved as a calendar file", path.name)

    def on_saved(self, kind, title, detail):
        self.busy = False
        color = {"saved": OK, "fallback": WARN, "error": ERR}[kind]
        self.set_card(color, title, color, detail, "")
        self.date_lbl.config(fg=DIM)
        if kind != "error":
            self.root.after(1400 if kind == "fallback" else 1000, self.hide)

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

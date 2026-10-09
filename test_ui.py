"""Drives the real popup window against a throwaway vault/settings and a stubbed calendar call.

    python test_ui.py

It briefly shows the popup on your screen. The calendar call is stubbed, so no account is contacted, and
the real ~/.quickadd.* files are never touched. Do not type while it runs: stray keys end up in its window.
"""
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

import calendars
import eventout
import quickadd
import settings
import vault

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition)))
    extra = "   [%s]" % (detail,) if detail and not condition else ""
    print("%-4s %s%s" % ("ok" if condition else "FAIL", name, extra))


def redirect(tmp):
    """Point every real path at the temp folder. Called before anything else can write."""
    import google_cal
    vault.VAULT, settings.CONFIG, eventout.OUT_DIR = tmp / "v.vault", tmp / "c.json", tmp / "out"
    google_cal.CLIENT_FILE = tmp / "google_client.json"          # absent unless a flow ships one
    assert settings.CONFIG.parent == tmp and vault.VAULT.parent == tmp
    vault.lock()


def reset(tmp):
    redirect(tmp)
    import google_cal
    for f in (vault.VAULT, settings.CONFIG, google_cal.CLIENT_FILE):
        f.unlink(missing_ok=True)
    for f in (tmp / "out").glob("*") if (tmp / "out").exists() else []:
        f.unlink()


def make_popup(tmp):
    redirect(tmp)
    p = quickadd.Popup()
    p.root.unbind("<FocusOut>")                  # test only: another window taking focus must not hide it
    return p


def pump(p, seconds):
    end = time.time() + seconds
    while time.time() < end:
        p.root.update()
        time.sleep(0.02)


def card(p):
    return p.title_lbl.cget("text"), p.date_lbl.cget("text")


def type_and_enter(p, text):
    p.var.set(text)
    pump(p, 0.15)
    p.on_enter(None)


def run_unlock_flow(tmp):
    reset(tmp)
    settings.CONFIG.write_text(json.dumps({"apple_id": "me@icloud.com", "calendar": "School"}))
    saved = []
    calendars.add_event = lambda target, ev, ics: saved.append((target, ev["title"])) or "TestCalendar"
    p = make_popup(tmp)
    vault.create("my pass phrase", {"apple_password": "dummy"})
    vault.lock()
    p.show()
    pump(p, 0.2)
    check("footer names the default calendar", p.foot_lbl.cget("text") == "Apple Calendar", p.foot_lbl.cget("text"))

    type_and_enter(p, "dentist friday 3pm")
    pump(p, 0.2)
    check("locked vault asks for the passphrase", p.unlocking and p.entry.cget("show") == "•" and "Unlock" in card(p)[0], card(p))

    p.var.set("wrong one")
    p.on_enter(None)
    pump(p, 1.0)
    check("wrong passphrase is refused and stays in unlock mode", card(p)[0] == "Wrong passphrase" and p.unlocking and p.var.get() == "")

    p.on_escape(None)
    pump(p, 0.2)
    check("Esc returns to the event being typed", not p.unlocking and p.var.get() == "dentist friday 3pm" and p.entry.cget("show") == "")

    p.on_enter(None)
    pump(p, 0.2)
    p.var.set("my pass phrase")
    p.on_enter(None)
    pump(p, 1.3)
    check("right passphrase saves the event", saved == [("apple", "Dentist")] and card(p)[0] == "Added to Apple Calendar", (saved, card(p)))
    pump(p, 1.5)
    check("popup hides itself after saving", p.root.state() == "withdrawn")

    p.show()
    type_and_enter(p, "lunch tomorrow noon")
    pump(p, 1.0)
    check("second event needs no passphrase", not p.unlocking and len(saved) == 2 and saved[1] == ("apple", "Lunch"), (saved, p.unlocking))
    p.root.destroy()


def run_no_calendar_flow(tmp):
    reset(tmp)
    saved = []
    calendars.add_event = lambda target, ev, ics: saved.append(target) or "x"
    p = make_popup(tmp)
    p.show()
    pump(p, 0.2)
    check("footer says a file when no calendar is set up", "quickadd" in p.foot_lbl.cget("text"), p.foot_lbl.cget("text"))
    type_and_enter(p, "dentist friday 3pm")
    pump(p, 0.5)
    files = list((tmp / "out").glob("*.ics"))
    check("no calendar: a .ics file is written and no calendar is called", len(files) == 1 and not saved, (files, saved))
    p.root.destroy()


def run_commands_flow(tmp):
    reset(tmp)
    settings.CONFIG.write_text(json.dumps({"apple_id": "me@icloud.com", "calendar": "School",
                                           "google": {"client_id": "x", "calendar_id": "primary", "calendar_name": "Me"}}))
    saved = []
    calendars.add_event = lambda target, ev, ics: saved.append((target, ev["title"])) or "Cal"
    p = make_popup(tmp)
    vault.create("my pass phrase", {"apple_password": "dummy"})
    p.show()
    pump(p, 0.2)

    p.var.set("/st")
    pump(p, 0.2)
    check("typing /st suggests /status", card(p)[0].startswith("/status"), card(p))
    p.on_tab(None)
    check("Tab completes the command", p.var.get() == "/status ", p.var.get())
    p.on_enter(None)
    pump(p, 0.2)
    check("/status shows the default calendar and vault state", card(p)[0] == "Status" and "Default: Apple Calendar" in card(p)[1]
          and "unlocked" in card(p)[1], card(p))

    p.var.set("/zzz")
    p.on_enter(None)
    pump(p, 0.2)
    check("an unknown command is an error, not an event", card(p)[0] == "No command /zzz" and not saved, card(p))

    p.var.set("/google")
    p.on_enter(None)
    pump(p, 0.2)
    check("/google makes Google the default and the footer follows",
          settings.load()["default_target"] == "google", settings.load().get("default_target"))
    pump(p, 1.2)
    p.show()
    check("footer shows the new default after reopening", p.foot_lbl.cget("text") == "Google Calendar", p.foot_lbl.cget("text"))

    p.var.set("dentist friday 3pm /apple")
    pump(p, 0.2)
    note = p.note_lbl.cget("text")
    check("a trailing /apple is shown as a one-off and the footer follows",
          "Apple Calendar (this event only)" in note and p.foot_lbl.cget("text") == "Apple Calendar" and card(p)[0] == "Dentist", (note, card(p)))
    p.on_enter(None)
    pump(p, 1.0)
    check("the one-off goes to Apple while Google stays the default",
          saved == [("apple", "Dentist")] and settings.load()["default_target"] == "google", (saved, settings.load().get("default_target")))
    pump(p, 1.5)

    p.show()
    type_and_enter(p, "lunch tomorrow noon")
    pump(p, 1.0)
    check("without an override the default (Google) is used", saved[-1] == ("google", "Lunch"), saved)
    pump(p, 1.5)

    p.show()
    p.var.set("/lock")
    p.on_enter(None)
    pump(p, 0.2)
    check("/lock forgets the passphrase", vault.needs_unlock() and card(p)[0] == "Locked", card(p))
    pump(p, 1.5)

    p.show()
    type_and_enter(p, "call mom tomorrow 6pm")
    pump(p, 0.3)
    check("after /lock the next event asks for the passphrase again", p.unlocking and "Google Calendar" in card(p)[0], card(p))
    p.on_escape(None)

    called = []
    p.restart, p.quit = (lambda: called.append("restart")), (lambda: called.append("quit"))
    for text in ("/restart", "/quit"):
        p.show()
        p.var.set(text)
        p.on_enter(None)
        pump(p, 0.8)
    check("/restart and /quit trigger their actions", called == ["restart", "quit"], called)
    p.root.destroy()


def run_override_not_configured_flow(tmp):
    reset(tmp)
    settings.CONFIG.write_text(json.dumps({"apple_id": "me@icloud.com", "calendar": "School"}))
    saved = []
    calendars.add_event = lambda target, ev, ics: saved.append(target) or "Cal"
    p = make_popup(tmp)
    p.show()
    p.var.set("dentist friday 3pm /google")
    pump(p, 0.2)
    check("an override to a calendar that is not set up says so", "Google Calendar is not set up, using Apple Calendar" in p.note_lbl.cget("text"),
          p.note_lbl.cget("text"))
    p.on_enter(None)
    pump(p, 1.0)
    check("and the event still goes to the default", saved == ["apple"], saved)
    p.root.destroy()


class _Resp:
    status_code = 200

    def json(self):
        return {}


class _Http:
    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return _Resp()


def run_settings_flow(tmp):
    import eventparser
    import google_cal
    import icloud
    import quickadd_ctl
    import settings_window as sw

    reset(tmp)
    vault.UNLOCK_IDLE_SECONDS = 1800
    prompts = []
    answers = ["pass-phrase-1"]
    sw.ask_passphrase = lambda parent, title, prompt, confirm=False: prompts.append((title, confirm)) or (answers.pop(0) if answers else None)
    asked = []
    icloud.list_calendars = lambda apple_id, password: asked.append((apple_id, password)) or ["School", "Home"]
    google_cal.sign_in = lambda client_id, secret, cancel=None: {"refresh_token": "R-tok", "access_token": "A", "expires_in": 3600}
    google_cal.list_calendars = lambda: [{"id": "me@gmail.com", "name": "me@gmail.com", "primary": True},
                                         {"id": "team@group", "name": "Team", "primary": False}]
    fake_http = _Http()
    google_cal._http = fake_http
    autostart = {"on": False}
    quickadd_ctl.autostart_enabled = lambda: autostart["on"]
    quickadd_ctl.set_autostart = lambda on: autostart.update(on=on)

    p = make_popup(tmp)
    restarted = []
    p.restart = lambda: restarted.append(1)
    check("settings window is not open yet", sw._open["win"] is None)
    p.show()
    p.var.set("/settings")
    p.on_enter(None)
    pump(p, 0.5)
    w = sw._open["win"]
    check("typing /settings opens the window", w is not None and w.alive() and w.top.winfo_viewable())
    pump(p, 1.2)
    check("and the popup hides itself", p.root.state() == "withdrawn")
    w.close()
    p.events.put("tray-settings")
    pump(p, 0.6)
    check("the tray's Settings item opens it too", sw._open["win"] is not None and sw._open["win"].alive())
    w = sw.open_window(p)
    pump(p, 0.3)
    check("opens even though the popup itself is hidden", w.top.winfo_viewable() and p.root.state() == "withdrawn")
    check("opening twice returns the same window", sw.open_window(p) is w)
    check("an empty setup says not connected", "Not connected" in w.apple_state.cget("text") and "Not connected" in w.google_state.cget("text"))

    # --- Apple: connect, then save (first save creates the vault and asks for a passphrase)
    w.apple_id_var.set("me@icloud.com")
    w.apple_save()
    check("saving before connecting is refused", "Connect" in w.apple_msg.cget("text") or "password" in w.apple_msg.cget("text"), w.apple_msg.cget("text"))
    w.apple_pw_var.set("abcd-efgh-ijkl-mnop")
    w.apple_connect()
    pump(p, 0.5)
    check("Connect loads the calendars", asked == [("me@icloud.com", "abcd-efgh-ijkl-mnop")] and list(w.apple_cal.cget("values")) == ["School", "Home"],
          (asked, w.apple_cal.cget("values")))
    w.apple_cal_var.set("Home")
    w.apple_save()
    check("first save creates the vault after asking for a passphrase", prompts == [("Create a passphrase", True)] and vault.is_unlocked(), prompts)
    cfg = settings.load()
    check("the Apple ID and calendar are saved, the password only in the vault",
          cfg["apple_id"] == "me@icloud.com" and cfg["calendar"] == "Home" and "abcd" not in settings.CONFIG.read_text()
          and vault.get("apple_password") == "abcd-efgh-ijkl-mnop" and w.apple_pw_var.get() == "", cfg)
    check("the status line shows it as connected", "me@icloud.com" in w.apple_state.cget("text") and "Home" in w.apple_state.cget("text"))

    # --- changing the calendar later reuses the stored password
    asked.clear()
    w.apple_connect()
    pump(p, 0.5)
    check("Connect with an empty password field reuses the stored one", asked == [("me@icloud.com", "abcd-efgh-ijkl-mnop")], asked)
    w.apple_cal_var.set("School")
    w.apple_save()
    check("calendar changed without retyping the password", settings.load()["calendar"] == "School" and vault.get("apple_password") == "abcd-efgh-ijkl-mnop")

    # --- Google: sign in, pick a calendar
    w.google_sign_in()
    check("Sign in needs the client ID and secret first", "client ID" in w.google_msg.cget("text"), w.google_msg.cget("text"))
    w.g_id_var.set("cid.apps.googleusercontent.com")
    w.g_secret_var.set("GOCSPX-secret")
    w.google_sign_in()
    pump(p, 0.6)
    check("sign-in stores the tokens in the vault, not in settings",
          vault.get("google_refresh_token") == "R-tok" and vault.get("google_client_secret") == "GOCSPX-secret"
          and "R-tok" not in settings.CONFIG.read_text() and "GOCSPX" not in settings.CONFIG.read_text())
    check("the client secret field is cleared after sign-in", w.g_secret_var.get() == "")
    check("the calendars are offered and the primary is preselected",
          list(w.g_cal.cget("values")) == ["me@gmail.com", "Team"] and w.g_cal_var.get() == "me@gmail.com", (w.g_cal.cget("values"), w.g_cal_var.get()))
    check("before choosing a calendar Google is not yet usable", not settings.google_configured())
    w.g_cal_var.set("Team")
    w.google_save_calendar()
    g = settings.load()["google"]
    check("saving the calendar makes Google usable", g == {"client_id": "cid.apps.googleusercontent.com", "own_client": True, "calendar_id": "team@group", "calendar_name": "Team"}
          and settings.google_configured() and "Connected: Team" in w.google_state.cget("text"), g)

    # --- Defaults
    check("both calendars are offered as the default", list(w.target_box.cget("values")) == ["Apple Calendar", "Google Calendar"])
    w.target_var.set("Google Calendar")
    w.duration_var.set("abc")
    w.save_defaults()
    check("a bad length is refused and nothing is saved", "whole numbers" in w.defaults_msg.cget("text") and settings.load()["default_target"] is None,
          w.defaults_msg.cget("text"))
    w.duration_var.set("45")
    w.lock_var.set("10")
    w.tz_auto_var.set(False)
    w.tz_var.set("Not/AZone")
    w.save_defaults()
    check("a bad timezone name is refused", "not a timezone" in w.defaults_msg.cget("text"), w.defaults_msg.cget("text"))
    w.tz_var.set("Europe/Berlin")
    w.save_defaults()
    d = settings.load()
    check("defaults are saved", d["default_target"] == "google" and d["defaults"]["duration_min"] == 45 and d["defaults"]["unlock_idle_min"] == 10
          and d["defaults"]["timezone"] == "Europe/Berlin", d)
    check("and applied at once", eventparser.DEFAULT_MINUTES == 45 and vault.UNLOCK_IDLE_SECONDS == 600, (eventparser.DEFAULT_MINUTES, vault.UNLOCK_IDLE_SECONDS))
    w.tz_auto_var.set(True)
    w.save_defaults()
    check("automatic timezone disables the field", settings.load()["defaults"]["timezone"] == "auto" and str(w.tz_entry.cget("state")) == "disabled")

    # --- General
    w.hotkey_var.set("C")
    w.save_hotkey()
    check("an invalid hotkey is refused", "Not a valid hotkey" in w.hotkey_msg.cget("text") and settings.load()["defaults"]["hotkey"] == "ctrl+alt+C")
    w.hotkey_var.set("ctrl+shift+7")
    w.save_hotkey()
    check("a valid hotkey is saved", settings.load()["defaults"]["hotkey"] == "ctrl+shift+7" and "Restart" in w.hotkey_msg.cget("text"))
    w.autostart_var.set(True)
    w.toggle_autostart()
    check("the autostart switch calls the startup helper", autostart["on"] is True)
    w.lock_now()
    check("Lock now locks the vault", vault.needs_unlock())
    w.restart_app()
    pump(p, 0.6)
    check("Restart asks the popup to restart", restarted == [1])

    # --- Disconnect (two clicks), both calendars
    answers[:] = ["wrong-one", "pass-phrase-1"]
    prompts.clear()
    w.g_off_btn.invoke()
    check("the first click only arms the button", w.g_off_btn.cget("text") == "Click again to confirm" and settings.google_configured())
    w.g_off_btn.invoke()
    pump(p, 0.6)
    check("a wrong passphrase is retried, then Google is disconnected and revoked",
          len(prompts) == 2 and not settings.google_configured() and settings.load()["google"] == {}
          and fake_http.posts and fake_http.posts[0][1]["data"] == {"token": "R-tok"} and vault.names() == ["apple_password"],
          (prompts, settings.load()["google"], vault.names() if vault.is_unlocked() else "locked"))
    check("the default falls back to Apple", settings.default_target() == "apple" and settings.load()["default_target"] is None)
    w.apple_off_btn.invoke()
    w.apple_off_btn.invoke()
    check("Apple is disconnected and its password removed", not settings.apple_configured() and vault.names() == [])
    check("the window shows both as not connected again", "Not connected" in w.apple_state.cget("text") and "Not connected" in w.google_state.cget("text"))

    w.close()
    check("closing destroys the window and a new one can be opened", not w.alive() and sw._open["win"] is None and sw.open_window(p) is not w)
    sw._open["win"].close()
    p.root.destroy()


def run_shipped_client_flow(tmp):
    """The normal-user path: the app ships one Google client, so the user only presses 'Sign in with Google'."""
    import google_cal
    import settings_window as sw

    reset(tmp)
    google_cal.CLIENT_FILE.write_text(json.dumps({"client_id": "123456789012-abcdefg.apps.googleusercontent.com", "client_secret": "GOCSPX-shipped"}))
    answers = ["pass-phrase-1"]
    sw.ask_passphrase = lambda parent, title, prompt, confirm=False: answers.pop(0) if answers else None
    seen = []
    google_cal.sign_in = lambda client_id, secret, cancel=None: seen.append((client_id, secret)) or {
        "refresh_token": "R-tok", "access_token": "A", "expires_in": 3600}
    google_cal.list_calendars = lambda: [{"id": "me@gmail.com", "name": "me@gmail.com", "primary": True}]
    google_cal._http = _Http()

    p = make_popup(tmp)
    w = sw.open_window(p)
    pump(p, 0.4)
    check("with a shipped client the client ID/secret fields are hidden", not w.g_adv.winfo_ismapped() and not w.g_adv.winfo_viewable(),
          (w.g_adv.winfo_ismapped(), ))
    check("the advanced switch is offered", "own Google project" in w.g_own_btn.cget("text"), w.g_own_btn.cget("text"))
    check("the status invites a single click", "Sign in with Google" in w.google_state.cget("text"), w.google_state.cget("text"))

    w.google_sign_in()                                           # nothing typed anywhere
    pump(p, 0.8)
    check("sign-in needs no typing and uses the shipped client", seen == [("123456789012-abcdefg.apps.googleusercontent.com", "GOCSPX-shipped")], seen)
    check("only the refresh token is stored; the shared secret is not copied", vault.names() == ["google_refresh_token"], vault.names())
    check("settings remember the shared client, not an own project",
          settings.load()["google"] == {"client_id": "123456789012-abcdefg.apps.googleusercontent.com", "own_client": False}, settings.load()["google"])
    check("the calendar list appears with the primary selected", w.g_cal_var.get() == "me@gmail.com", w.g_cal_var.get())
    w.google_save_calendar()
    check("saving the calendar makes Google usable", settings.google_configured() and "Connected: me@gmail.com" in w.google_state.cget("text"))

    w.toggle_own()
    pump(p, 0.2)
    check("the advanced switch reveals the fields for an own project", w.g_adv.winfo_ismapped() and "built-in" in w.g_own_btn.cget("text"), w.g_own_btn.cget("text"))
    w.g_id_var.set("")
    w.g_secret_var.set("")
    w.google_sign_in()
    check("in own-project mode the fields are required", "client ID" in w.google_msg.cget("text"), w.google_msg.cget("text"))
    w.toggle_own()
    pump(p, 0.2)
    check("switching back hides them again", not w.g_adv.winfo_ismapped())

    w.close()
    sw._open["win"] = None
    google_cal.CLIENT_FILE.unlink()
    w = sw.open_window(p)
    pump(p, 0.4)
    check("with no shipped client the own-project fields are shown and explained",
          w.g_adv.winfo_ismapped() and "no built-in" in w.g_own_btn.cget("text"), w.g_own_btn.cget("text"))
    w.close()
    p.root.destroy()


def main():
    only = sys.argv[1:] or None
    flows = [("unlock flow", run_unlock_flow), ("no-calendar flow", run_no_calendar_flow),
             ("commands flow", run_commands_flow), ("override flow", run_override_not_configured_flow),
             ("settings flow", run_settings_flow), ("shipped client flow", run_shipped_client_flow)]
    real_add = calendars.add_event
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        redirect(tmp)                                  # first thing: from here nothing can reach the real files
        try:
            for name, fn in flows:
                if only and name not in only:
                    continue
                try:
                    fn(tmp)
                except Exception:  # noqa: BLE001
                    check(name + " ran without an error", False, traceback.format_exc().splitlines()[-1])
        finally:
            calendars.add_event = real_add
            vault.lock()
    failed = sum(1 for _, ok in RESULTS if not ok)
    print("\n%d/%d passed" % (len(RESULTS) - failed, len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

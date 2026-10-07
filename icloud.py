"""Add events to Apple Calendar (iCloud) over CalDAV. Works from Windows; no Apple device needed.

One-time setup (asks for your Apple ID and an app-specific password, never your normal password):

    python icloud.py setup

The app-specific password is kept in Windows Credential Manager (via keyring). Only the Apple ID
and chosen calendar name go in ~/.quickadd.json. Create the password at appleid.apple.com ->
Sign-In and Security -> App-Specific Passwords; revoke it there any time.

Other commands:  python icloud.py status   |   python icloud.py forget
"""
import getpass
import json
import sys
from pathlib import Path

CONFIG = Path.home() / ".quickadd.json"
SERVICE = "quickadd-icloud"
URL = "https://caldav.icloud.com"


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_configured():
    cfg = load_config()
    return bool(cfg and cfg.get("apple_id") and cfg.get("calendar"))


def _client(apple_id, password):
    import caldav  # imported lazily: keeps the idle popup small
    return caldav.DAVClient(url=URL, username=apple_id, password=password)


def _writable_calendars(client):
    out = []
    for cal in client.principal().calendars():
        try:
            supported = cal.get_supported_components()
        except Exception:
            supported = ["VEVENT"]
        if "VEVENT" in supported:  # skips Reminders lists
            out.append(cal)
    return out


def add_event(ics_text):
    """Save one event (iCalendar text) to the configured calendar. Returns the calendar name.
    Raises RuntimeError with a readable message on any failure."""
    import keyring
    cfg = load_config()
    if not cfg:
        raise RuntimeError("iCloud is not set up (run: python icloud.py setup)")
    password = keyring.get_password(SERVICE, cfg["apple_id"])
    if not password:
        raise RuntimeError("no stored iCloud password (run: python icloud.py setup)")
    try:
        client = _client(cfg["apple_id"], password)
        for cal in _writable_calendars(client):
            if (cal.name or "") == cfg["calendar"]:
                cal.save_event(ics_text)
                return cfg["calendar"]
    except Exception as err:  # caldav raises many types; the caller only needs the text
        raise RuntimeError("iCloud: %s" % (str(err) or err.__class__.__name__)) from err
    raise RuntimeError("calendar %r not found on this account" % cfg["calendar"])


def setup():
    import keyring
    apple_id = input("Apple ID (email): ").strip()
    password = getpass.getpass("App-specific password (hidden): ").strip()
    print("Connecting to iCloud ...")
    try:
        calendars = _writable_calendars(_client(apple_id, password))
    except Exception as err:
        raise SystemExit("Could not connect: %s\nCheck the Apple ID and that this is an app-specific "
                         "password (xxxx-xxxx-xxxx-xxxx), not your normal one." % (str(err) or err.__class__.__name__))
    names = [c.name or "(unnamed)" for c in calendars]
    if not names:
        raise SystemExit("Connected, but found no event calendars.")
    print("\nCalendars:")
    for i, name in enumerate(names, 1):
        print("  %d. %s" % (i, name))
    pick = input("Which one should quick-add use? [1] ").strip() or "1"
    if not (pick.isdigit() and 1 <= int(pick) <= len(names)):
        raise SystemExit("Not a valid choice.")
    keyring.set_password(SERVICE, apple_id, password)
    CONFIG.write_text(json.dumps({"apple_id": apple_id, "calendar": names[int(pick) - 1]}, indent=2),
                      encoding="utf-8")
    print("\nSaved. Quick-add will now add events to %r." % names[int(pick) - 1])


def forget():
    import keyring
    cfg = load_config()
    if cfg:
        try:
            keyring.delete_password(SERVICE, cfg["apple_id"])
        except Exception:
            pass
        CONFIG.unlink(missing_ok=True)
    print("Removed the stored iCloud login and settings.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        setup()
    elif cmd == "forget":
        forget()
    else:
        cfg = load_config()
        print("configured: %s" % (("%s -> %s" % (cfg["apple_id"], cfg["calendar"])) if cfg else "no"))

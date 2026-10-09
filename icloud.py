"""Add events to Apple Calendar (iCloud) over CalDAV. Works from Windows; no Apple device needed.

The app-specific password lives in the passphrase vault (vault.py, ~/.bamboo.vault) and the Apple ID
and calendar name in ~/.bamboo.json (settings.py). The popup asks for the passphrase once, then keeps
the password in memory for a while (see vault.UNLOCK_IDLE_SECONDS).

    python icloud.py setup       # Apple ID, app-specific password, passphrase
    python icloud.py protect     # move an old Credential Manager password into the vault
    python icloud.py status
    python icloud.py forget      # remove Apple Calendar (add --all to wipe the vault and settings too)

Create the app-specific password at appleid.apple.com -> Sign-In and Security -> App-Specific
Passwords; revoke it there any time. `setup --keyring` keeps the old behaviour (Windows Credential
Manager, no passphrase).
"""
import getpass
import sys

import paths
import settings
import vault
from vault import Locked, has_vault, is_unlocked, lock, needs_unlock, unlock  # noqa: F401 - re-exported

SERVICE = paths.KEYRING_SERVICE
URL = "https://caldav.icloud.com"
SECRET = "apple_password"

load_config = settings.load
is_configured = settings.apple_configured


def _password(cfg):
    if vault.has_vault():
        password = vault.get(SECRET)                     # raises Locked until the passphrase is entered
    else:
        import keyring
        password = keyring.get_password(SERVICE, cfg["apple_id"])
    if not password:
        raise RuntimeError("no stored iCloud password (run: python icloud.py setup)")
    return password


# ------------------------------------------------------------------ CalDAV

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


def list_calendars(apple_id, password):
    """Names of the event calendars on this account (raises on a bad login)."""
    return [c.name or "(unnamed)" for c in _writable_calendars(_client(apple_id, password))]


def add_event(ics_text):
    """Save one event (iCalendar text) to the configured calendar. Returns the calendar name.
    Raises Locked when the passphrase is needed, RuntimeError with a readable message otherwise."""
    cfg = settings.load()
    if not settings.apple_configured(cfg):
        raise RuntimeError("Apple Calendar is not set up")
    password = _password(cfg)
    try:
        client = _client(cfg["apple_id"], password)
        for cal in _writable_calendars(client):
            if (cal.name or "") == cfg["calendar"]:
                cal.save_event(ics_text)
                return cfg["calendar"]
    except Exception as err:  # caldav raises many types; the caller only needs the text
        raise RuntimeError("iCloud: %s" % (str(err) or err.__class__.__name__)) from err
    raise RuntimeError("calendar %r not found on this account" % cfg["calendar"])


# ------------------------------------------------------------------ command line

def _ask_new_passphrase():
    while True:
        first = getpass.getpass("Choose a passphrase (%d+ characters, only you know it): " % vault.MIN_PASSPHRASE)
        if len(first) < vault.MIN_PASSPHRASE:
            print("Too short.")
            continue
        if getpass.getpass("Repeat it: ") != first:
            print("They do not match.")
            continue
        return first


def _store_password(password):
    """Put the Apple password in the vault, creating it or unlocking the existing one."""
    if not vault.has_vault():
        vault.create(_ask_new_passphrase(), {SECRET: password})
        return
    if not vault.is_unlocked() and not vault.unlock(getpass.getpass("Your existing vault passphrase: ")):
        raise SystemExit("Wrong passphrase, nothing was changed.")
    vault.put(SECRET, password)


def setup(use_keyring=False):
    apple_id = input("Apple ID (email): ").strip()
    password = getpass.getpass("App-specific password (hidden): ").strip()
    print("Connecting to iCloud ...")
    try:
        names = list_calendars(apple_id, password)
    except Exception as err:
        raise SystemExit("Could not connect: %s\nCheck the Apple ID and that this is an app-specific "
                         "password (xxxx-xxxx-xxxx-xxxx), not your normal one." % (str(err) or err.__class__.__name__))
    if not names:
        raise SystemExit("Connected, but found no event calendars.")
    print("\nCalendars:")
    for i, name in enumerate(names, 1):
        print("  %d. %s" % (i, name))
    pick = input("Which one should Bamboo use? [1] ").strip() or "1"
    if not (pick.isdigit() and 1 <= int(pick) <= len(names)):
        raise SystemExit("Not a valid choice.")
    if use_keyring:
        import keyring
        keyring.set_password(SERVICE, apple_id, password)
    else:
        _store_password(password)
    settings.update({"apple_id": apple_id, "calendar": names[int(pick) - 1]})
    print("\nSaved. Bamboo can now add events to %r." % names[int(pick) - 1])


def protect():
    """Move a password that sits in Windows Credential Manager into the passphrase vault."""
    import keyring
    cfg = settings.load()
    if not settings.apple_configured(cfg):
        raise SystemExit("Nothing to protect: run setup first.")
    password = keyring.get_password(SERVICE, cfg["apple_id"])
    if not password:
        raise SystemExit("No password in Credential Manager for %s." % cfg["apple_id"])
    _store_password(password)
    keyring.delete_password(SERVICE, cfg["apple_id"])
    print("Done. The password is now encrypted in %s and removed from Credential Manager." % vault.VAULT)


def forget(everything=False):
    cfg = settings.load()
    if cfg.get("apple_id"):
        try:
            import keyring
            keyring.delete_password(SERVICE, cfg["apple_id"])
        except Exception:
            pass
    if everything:
        vault.destroy()
        settings.CONFIG.unlink(missing_ok=True)
        print("Removed the vault and all settings.")
        return
    cfg.pop("apple_id", None)
    cfg.pop("calendar", None)
    settings.save(cfg)
    if vault.has_vault() and (vault.is_unlocked() or vault.unlock(getpass.getpass("Vault passphrase (to remove the Apple password): "))):
        vault.delete(SECRET)
        if not vault.names():
            vault.destroy()
    print("Removed Apple Calendar.")


def status():
    cfg = settings.load()
    if not settings.apple_configured(cfg):
        print("configured: no")
        return
    where = "passphrase vault" if has_vault() else "Windows Credential Manager (no passphrase)"
    print("configured: %s -> %s\npassword stored in: %s" % (cfg["apple_id"], cfg["calendar"], where))


if __name__ == "__main__":
    paths.migrate()                                    # first run after the rename: copy old settings and vault
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        setup(use_keyring="--keyring" in sys.argv)
    elif cmd == "protect":
        protect()
    elif cmd == "forget":
        forget(everything="--all" in sys.argv)
    else:
        status()

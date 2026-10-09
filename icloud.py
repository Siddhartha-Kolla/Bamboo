"""Add events to Apple Calendar (iCloud) over CalDAV. Works from Windows; no Apple device needed.

The app-specific password is kept ENCRYPTED with a passphrase only you know (scrypt + Fernet), in
~/.quickadd.vault. The popup asks for the passphrase once, keeps the password in memory for
UNLOCK_IDLE_SECONDS after the last use, then forgets it and asks again.

    python icloud.py setup       # new setup: Apple ID, app-specific password, passphrase
    python icloud.py protect     # move an existing Credential Manager password into the vault
    python icloud.py status
    python icloud.py forget      # delete the vault, any stored password and the settings

Create the app-specific password at appleid.apple.com -> Sign-In and Security -> App-Specific
Passwords; revoke it there any time. Only the Apple ID and calendar name sit in plain text, in
~/.quickadd.json. `setup --keyring` keeps the old behaviour (Windows Credential Manager, no passphrase).
"""
import base64
import getpass
import hashlib
import json
import os
import sys
import time
from pathlib import Path

CONFIG = Path.home() / ".quickadd.json"
VAULT = Path.home() / ".quickadd.vault"
SERVICE = "quickadd-icloud"
URL = "https://caldav.icloud.com"

UNLOCK_IDLE_SECONDS = 30 * 60      # forget the password after this long without a save
MIN_PASSPHRASE = 8
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}   # ~32 MB and ~0.1 s per guess for anyone trying to crack the vault

_unlocked = {"password": None, "until": 0.0}


class Locked(RuntimeError):
    """The password is in the vault and the passphrase has not been entered (or has expired)."""


# ------------------------------------------------------------------ vault (passphrase encryption)

def _key(passphrase, salt, n, r, p):
    raw = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 * 1024 * 1024)
    return base64.urlsafe_b64encode(raw)


def has_vault(path=None):
    return Path(path or VAULT).exists()


def encrypt_secret(secret, passphrase, path=None):
    from cryptography.fernet import Fernet
    path = path or VAULT
    salt = os.urandom(16)
    token = Fernet(_key(passphrase, salt, **SCRYPT)).encrypt(secret.encode("utf-8"))
    blob = {"v": 1, "kdf": "scrypt", **SCRYPT, "salt": base64.b64encode(salt).decode(), "token": token.decode()}
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(blob), encoding="utf-8")
    os.replace(tmp, path)


def decrypt_secret(passphrase, path=None):
    """The stored secret, or ValueError when the passphrase is wrong or the file was altered."""
    from cryptography.fernet import Fernet, InvalidToken
    try:
        blob = json.loads(Path(path or VAULT).read_text(encoding="utf-8"))
        key = _key(passphrase, base64.b64decode(blob["salt"]), blob["n"], blob["r"], blob["p"])
        return Fernet(key).decrypt(blob["token"].encode()).decode("utf-8")
    except (InvalidToken, KeyError, ValueError, TypeError, OSError) as err:
        raise ValueError("wrong passphrase, or the vault file is damaged") from err


def is_unlocked():
    return _unlocked["password"] is not None and time.monotonic() < _unlocked["until"]


def needs_unlock():
    return has_vault() and not is_unlocked()


def unlock(passphrase):
    """True and the password is held in memory for UNLOCK_IDLE_SECONDS; False for a wrong passphrase."""
    try:
        password = decrypt_secret(passphrase)
    except ValueError:
        return False
    _unlocked["password"] = password
    _unlocked["until"] = time.monotonic() + UNLOCK_IDLE_SECONDS
    return True


def lock():
    _unlocked["password"] = None
    _unlocked["until"] = 0.0


def _password(cfg):
    if is_unlocked():
        _unlocked["until"] = time.monotonic() + UNLOCK_IDLE_SECONDS     # in use: keep it a while longer
        return _unlocked["password"]
    lock()                                                             # expired: drop the reference
    if has_vault():
        raise Locked("iCloud is locked: enter your passphrase")
    import keyring
    password = keyring.get_password(SERVICE, cfg["apple_id"])
    if not password:
        raise RuntimeError("no stored iCloud password (run: python icloud.py setup)")
    return password


# ------------------------------------------------------------------ CalDAV

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
    Raises Locked when the passphrase is needed, RuntimeError with a readable message otherwise."""
    cfg = load_config()
    if not cfg:
        raise RuntimeError("iCloud is not set up (run: python icloud.py setup)")
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

def _ask_passphrase():
    while True:
        first = getpass.getpass("Choose a passphrase (%d+ characters, only you know it): " % MIN_PASSPHRASE)
        if len(first) < MIN_PASSPHRASE:
            print("Too short.")
            continue
        if getpass.getpass("Repeat it: ") != first:
            print("They do not match.")
            continue
        return first


def setup(use_keyring=False):
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
    if use_keyring:
        import keyring
        keyring.set_password(SERVICE, apple_id, password)
    else:
        encrypt_secret(password, _ask_passphrase())
    CONFIG.write_text(json.dumps({"apple_id": apple_id, "calendar": names[int(pick) - 1]}, indent=2),
                      encoding="utf-8")
    print("\nSaved. Quick-add will now add events to %r." % names[int(pick) - 1])


def protect():
    """Move a password that sits in Windows Credential Manager into the passphrase vault."""
    import keyring
    cfg = load_config()
    if not cfg:
        raise SystemExit("Nothing to protect: run setup first.")
    password = keyring.get_password(SERVICE, cfg["apple_id"])
    if not password:
        raise SystemExit("No password in Credential Manager for %s." % cfg["apple_id"])
    encrypt_secret(password, _ask_passphrase())
    if not unlock(getpass.getpass("Type the passphrase once more to check it: ")):
        VAULT.unlink(missing_ok=True)
        raise SystemExit("That did not match, so nothing was changed.")
    keyring.delete_password(SERVICE, cfg["apple_id"])
    print("Done. The password is now encrypted in %s and removed from Credential Manager." % VAULT)


def forget():
    cfg = load_config()
    if cfg:
        try:
            import keyring
            keyring.delete_password(SERVICE, cfg["apple_id"])
        except Exception:
            pass
    VAULT.unlink(missing_ok=True)
    CONFIG.unlink(missing_ok=True)
    print("Removed the vault, any stored password and the settings.")


def status():
    cfg = load_config()
    if not cfg:
        print("configured: no")
        return
    where = "passphrase vault" if has_vault() else "Windows Credential Manager (no passphrase)"
    print("configured: %s -> %s\npassword stored in: %s" % (cfg["apple_id"], cfg["calendar"], where))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        setup(use_keyring="--keyring" in sys.argv)
    elif cmd == "protect":
        protect()
    elif cmd == "forget":
        forget()
    else:
        status()

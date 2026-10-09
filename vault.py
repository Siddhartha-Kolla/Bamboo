"""One passphrase-encrypted file for every secret the tool keeps (Apple password, Google tokens).

scrypt turns your passphrase into a key; Fernet (AES plus an integrity check) encrypts a JSON object of
named secrets into ~/.bamboo.vault. One passphrase unlocks all of them.

After a successful unlock the derived KEY (never the passphrase) stays in memory so secrets can be read
and re-saved without asking again, and is dropped UNLOCK_IDLE_SECONDS after the last use. The key opens
only this vault. Python cannot reliably wipe strings, so "dropped" means the references are released.

File format: {"v": 2, "kdf": "scrypt", n, r, p, "salt": b64, "token": fernet}. Version 1 held a single
string (the Apple password); it is read as {"apple_password": ...} and upgraded on the first unlock,
after a one-time copy to .bamboo.vault.bak.
"""
import base64
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import paths

VAULT = paths.VAULT
UNLOCK_IDLE_SECONDS = 30 * 60
MIN_PASSPHRASE = 8
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}      # ~32 MB and ~0.1 s per guess for anyone trying to crack the file

_state = {"secrets": None, "key": None, "salt": None, "params": None, "until": 0.0}


class Locked(RuntimeError):
    """The secrets are in the vault and the passphrase has not been entered (or has expired)."""


def _derive(passphrase, salt, params):
    raw = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=params["n"], r=params["r"], p=params["p"],
                         dklen=32, maxmem=256 * 1024 * 1024)
    return base64.urlsafe_b64encode(raw)


def has_vault(path=None):
    return Path(path or VAULT).exists()


def is_unlocked():
    return _state["secrets"] is not None and time.monotonic() < _state["until"]


def needs_unlock():
    return has_vault() and not is_unlocked()


def seconds_left():
    """Seconds until an unlocked vault locks itself again (0 when it is locked)."""
    return max(0, int(_state["until"] - time.monotonic())) if is_unlocked() else 0


def lock():
    _state.update(secrets=None, key=None, salt=None, params=None, until=0.0)


def _touch():
    _state["until"] = time.monotonic() + UNLOCK_IDLE_SECONDS


def _write(path, secrets, key, salt, params):
    from cryptography.fernet import Fernet
    token = Fernet(key).encrypt(json.dumps(secrets).encode("utf-8"))
    blob = {"v": 2, "kdf": "scrypt", **params, "salt": base64.b64encode(salt).decode(), "token": token.decode()}
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(blob), encoding="utf-8")
    os.replace(tmp, path)


def create(passphrase, secrets=None, path=None):
    """Make a new vault (replacing any file there) and leave it unlocked."""
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError("the passphrase needs at least %d characters" % MIN_PASSPHRASE)
    path = Path(path or VAULT)
    salt = os.urandom(16)
    key = _derive(passphrase, salt, SCRYPT)
    data = dict(secrets or {})
    _write(path, data, key, salt, dict(SCRYPT))
    _state.update(secrets=data, key=key, salt=salt, params=dict(SCRYPT))
    _touch()


def _decode(blob, key):
    from cryptography.fernet import Fernet
    plain = Fernet(key).decrypt(blob["token"].encode()).decode("utf-8")
    if blob.get("v") == 1:
        return {"apple_password": plain}, True
    data = json.loads(plain)
    if not isinstance(data, dict):
        raise ValueError("vault content is not an object")
    return data, False


def unlock(passphrase, path=None):
    """True (secrets now readable for UNLOCK_IDLE_SECONDS) or False for a wrong passphrase or damaged file."""
    from cryptography.fernet import InvalidToken
    path = Path(path or VAULT)
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        salt = base64.b64decode(blob["salt"])
        params = {"n": blob["n"], "r": blob["r"], "p": blob["p"]}
        key = _derive(passphrase, salt, params)
        secrets, was_v1 = _decode(blob, key)
    except (InvalidToken, KeyError, ValueError, TypeError, OSError):
        return False
    _state.update(secrets=secrets, key=key, salt=salt, params=params)
    _touch()
    if was_v1:                                        # upgrade in place, keeping the old file once
        try:
            backup = Path(str(path) + ".bak")
            if not backup.exists():
                shutil.copy2(path, backup)
            _write(path, secrets, key, salt, params)
        except OSError:
            pass                                      # still readable as v1; try again next unlock
    return True


def _require():
    if not is_unlocked():
        lock()                                        # expired: release the references
        raise Locked("locked: enter your passphrase")
    _touch()


def get(name, default=None):
    _require()
    return _state["secrets"].get(name, default)


def put(name, value, path=None):
    _require()
    _state["secrets"][name] = value
    _write(Path(path or VAULT), _state["secrets"], _state["key"], _state["salt"], _state["params"])


def delete(name, path=None):
    _require()
    _state["secrets"].pop(name, None)
    _write(Path(path or VAULT), _state["secrets"], _state["key"], _state["salt"], _state["params"])


def names():
    _require()
    return sorted(_state["secrets"])


def destroy(path=None):
    lock()
    Path(path or VAULT).unlink(missing_ok=True)

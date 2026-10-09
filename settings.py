"""Settings in ~/.bamboo.json. Plain text and never holds a secret (those live in vault.py).

The file keeps the keys older versions wrote (`apple_id`, `calendar`) at the top level, so nothing needs
migrating:

    {"apple_id": "...", "calendar": "...",                     # Apple Calendar
     "google": {"mode": "link"}  or  {"mode": "api", "client_id": "...", "calendar_id": "...", "calendar_name": "..."},
     "default_target": "apple" | "google" | null,
     "defaults": {"duration_min": 60, "unlock_idle_min": 30, "timezone": "auto", "hotkey": "ctrl+alt+C", "playful": true}}
"""
import json
import os
import re
from pathlib import Path

import paths

CONFIG = paths.CONFIG

DEFAULTS = {"duration_min": 60, "unlock_idle_min": 30, "timezone": "auto", "hotkey": "ctrl+alt+C", "playful": True}
TARGETS = ("apple", "google")
LABELS = {"apple": "Apple Calendar", "google": "Google Calendar"}

_HOTKEY = re.compile(r"^((?:ctrl|alt|shift|win)\+)+([a-z0-9]|f(?:[1-9]|1[0-2]))$", re.I)


def valid_hotkey(text):
    return bool(_HOTKEY.match(text or "")) and any(m in text.lower() for m in ("ctrl+", "alt+", "win+"))


def parse_hotkey(text):
    """"ctrl+alt+C" -> (["ctrl", "alt"], "C"); F-keys come back as "F5"."""
    parts = text.split("+")
    key = parts[-1]
    return [p.lower() for p in parts[:-1]], (key.upper() if len(key) > 1 else key.upper())


def _int_in(value, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if low <= number <= high else fallback


def load(path=None):
    """The settings with defaults filled in. Never raises: a missing or damaged file gives the defaults."""
    try:
        raw = json.loads(Path(path or CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    cfg = dict(raw)
    given = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    d = {**DEFAULTS, **given}
    d["duration_min"] = _int_in(d["duration_min"], 1, 24 * 60, DEFAULTS["duration_min"])
    d["unlock_idle_min"] = _int_in(d["unlock_idle_min"], 1, 24 * 60, DEFAULTS["unlock_idle_min"])
    if not valid_hotkey(str(d["hotkey"])):
        d["hotkey"] = DEFAULTS["hotkey"]
    if not isinstance(d["timezone"], str) or not d["timezone"]:
        d["timezone"] = "auto"
    if not isinstance(d["playful"], bool):
        d["playful"] = DEFAULTS["playful"]
    cfg["defaults"] = d
    cfg["google"] = dict(raw["google"]) if isinstance(raw.get("google"), dict) else {}
    if cfg.get("default_target") not in TARGETS:
        cfg["default_target"] = None
    return cfg


def save(cfg, path=None):
    path = Path(path or CONFIG)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def update(changes, path=None):
    """Merge `changes` (a dict, nested dicts merged one level deep) into the saved settings."""
    cfg = load(path)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    save(cfg, path)
    return cfg


def apple_configured(cfg=None):
    cfg = cfg or load()
    return bool(cfg.get("apple_id") and cfg.get("calendar"))


def google_mode(cfg=None):
    """"link" (prefilled browser link, no sign-in), "api" (signed in with an own Google project) or None.
    A google section written before modes existed (client_id but no mode) is "api"."""
    g = (cfg or load()).get("google") or {}
    if g.get("mode") in ("link", "api"):
        return g["mode"]
    return "api" if g.get("client_id") else None


def google_configured(cfg=None):
    cfg = cfg or load()
    g = cfg.get("google") or {}
    mode = google_mode(cfg)
    if mode == "link":
        return True
    return bool(mode == "api" and g.get("client_id") and g.get("calendar_id"))


def configured_targets(cfg=None):
    cfg = cfg or load()
    return [t for t, ok in (("apple", apple_configured(cfg)), ("google", google_configured(cfg))) if ok]


def default_target(cfg=None):
    """The chosen default if it is set up, else the first calendar that is, else None (save a file)."""
    cfg = cfg or load()
    ready = configured_targets(cfg)
    want = cfg.get("default_target")
    return want if want in ready else (ready[0] if ready else None)

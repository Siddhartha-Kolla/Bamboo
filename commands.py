"""Slash commands for the popup: type "/" instead of an event.

    /help  /settings  /status  /target  /apple  /google  /lock  /log  /restart  /quit

A command returns a Result; the popup shows it and carries out `action` (open the settings window,
restart, quit ...), so everything here can be tested without a window.

A trailing "/google" or "/apple" on an ordinary event ("dentist friday 3pm /google") is not a command:
split_override() removes it and says which calendar that one event should go to.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import calendars
import settings
import vault


@dataclass
class Result:
    kind: str                       # "info" | "ok" | "error"
    title: str
    detail: str = ""
    action: Optional[str] = None    # "settings" | "restart" | "quit" | "log" | None
    autohide: bool = False          # close the popup shortly after showing it


_OVERRIDE = re.compile(r"\s+/(apple|google)\s*$", re.I)


def split_override(text):
    """("dentist friday 3pm", "google") for "dentist friday 3pm /google"; (text, None) otherwise."""
    m = _OVERRIDE.search(text)
    if not m or text.lstrip().startswith("/"):
        return text, None
    return text[:m.start()], m.group(1).lower()


def is_command(text):
    return text.lstrip().startswith("/")


def _split(text):
    parts = text.strip().lstrip("/").split(None, 1)
    return (parts[0].lower() if parts else ""), (parts[1].strip() if len(parts) > 1 else "")


# ------------------------------------------------------------------------------------ handlers

def _help(_arg):
    return Result("info", "Commands", "   ".join("/" + n for n in COMMANDS) + "\n" + "Add /google or /apple after an event to send just that one there.")


def _settings(_arg):
    return Result("info", "Opening settings…", action="settings", autohide=True)


def _status(_arg):
    cfg = settings.load()
    ready = settings.configured_targets(cfg)
    target = calendars.choose_target(cfg)
    if not vault.has_vault():
        locked = "no vault yet"
    elif vault.is_unlocked():
        locked = "unlocked, locks in %d min" % (vault.seconds_left() // 60 + 1)
    else:
        locked = "locked"
    import eventout
    zone = eventout.local_zone()
    detail = "Default: %s   ·   Connected: %s\nVault: %s   ·   Timezone: %s   ·   Hotkey: %s" % (
        calendars.label(target) if target else "a local file", ", ".join(calendars.label(t) for t in ready) or "none",
        locked, zone[0] if zone else "UTC", cfg["defaults"]["hotkey"])
    return Result("info", "Status", detail)


def _set_target(name):
    cfg = settings.load()
    if name not in settings.configured_targets(cfg):
        return Result("error", "%s is not set up yet" % calendars.label(name), "Open /settings and connect it first.")
    settings.update({"default_target": name})
    return Result("ok", "Default calendar: %s" % calendars.label(name), autohide=True)


def _target(arg):
    arg = arg.lower()
    if arg in settings.TARGETS:
        return _set_target(arg)
    cfg = settings.load()
    ready = settings.configured_targets(cfg)
    current = calendars.choose_target(cfg)
    if arg:
        return Result("error", "Unknown calendar %r" % arg, "Use /target apple or /target google.")
    return Result("info", "Default calendar: %s" % (calendars.label(current) if current else "a local file"),
                  "Connected: %s   ·   change with /apple or /google" % (", ".join(calendars.label(t) for t in ready) or "none"))


def _lock(_arg):
    vault.lock()
    return Result("ok", "Locked", "The passphrase is needed again for the next event.", autohide=True)


def _log(_arg):
    return Result("info", "Opening the log…", str(Path.home() / ".quickadd.log"), action="log", autohide=True)


def _restart(_arg):
    return Result("info", "Restarting…", "Loading the latest code.", action="restart", autohide=True)


def _quit(_arg):
    return Result("info", "Quitting quick-add", action="quit", autohide=True)


COMMANDS = {
    "help": ("show the commands", _help),
    "settings": ("open the settings window", _settings),
    "status": ("default calendar, vault, timezone", _status),
    "target": ("show or set the default calendar", _target),
    "apple": ("make Apple Calendar the default", lambda arg: _set_target("apple")),
    "google": ("make Google Calendar the default", lambda arg: _set_target("google")),
    "lock": ("forget the passphrase now", _lock),
    "log": ("open the log file", _log),
    "restart": ("restart quick-add (loads code changes)", _restart),
    "quit": ("stop quick-add", _quit),
}


# ------------------------------------------------------------------------------------ typing aids

def matches(text):
    """Commands whose name starts with what has been typed after the slash, in menu order."""
    name, _arg = _split(text)
    return [n for n in COMMANDS if n.startswith(name)]


def complete(text):
    """The text with the command name completed as far as it is unambiguous (Tab); unchanged otherwise."""
    name, arg = _split(text)
    found = matches(text)
    if not found or arg or text.endswith(" "):          # nothing to complete, or already typing the argument
        return text
    if len(found) == 1:
        return "/%s " % found[0]
    common = found[0]
    for other in found[1:]:
        while not other.startswith(common):
            common = common[:-1]
    return "/" + common if len(common) > len(name) else text


def suggestions(text):
    """(title, detail) for the card while a command is being typed."""
    name, _arg = _split(text)
    found = matches(text)
    if not found:
        return "No command /%s" % name, "Type /help to see them."
    first = found[0]
    return "/%s  ·  %s" % (first, COMMANDS[first][0]), "   ".join("/" + n for n in found[:8]) + "     (Tab completes, Enter runs)"


def run(text):
    """Run the command typed in `text`. Unknown or ambiguous names give an error Result."""
    name, arg = _split(text)
    if name in COMMANDS:
        return COMMANDS[name][1](arg)
    found = matches(text)
    if len(found) == 1:
        return COMMANDS[found[0]][1](arg)               # "/sett" runs /settings
    if not name:
        return COMMANDS["help"][1]("")
    return Result("error", "No command /%s" % name, "Type /help to see them.")

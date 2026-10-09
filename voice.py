"""Bamboo's voice: short, warm lines for the popup's cards.

Every situation has a PLAIN line (exactly the app's original wording) and one to three friendlier ones.
Which set is used follows Settings -> General -> "Friendly messages" (default on). Lines are short so
they fit a card, carry no emoji (Tk draws them badly) and never hide the useful part: the calendar's
name is still in the line, and error details are shown below it by the caller.

    voice.say("saved", calendar="Apple Calendar")   ->  "Done! Tucked into Apple Calendar"
"""
import random

import settings

# key: (plain wording, [friendlier wordings])
LINES = {
    "saved": ("Added to {calendar}", ["Done! Tucked into {calendar}", "All set. {calendar} has it", "Added to {calendar}. Easy."]),
    "opened": ("Opened in {calendar}", ["Opened in {calendar}. Just press Save", "Over to {calendar}. Press Save there"]),
    "fallback": ("{calendar} failed, saved a file instead",
                 ["{calendar} wouldn't take it, so I saved a file", "No luck with {calendar}. Saved a file instead"]),
    "empty": ("Describe your event", ["What's coming up?", "Tell me what's coming up"]),
    "unlock": ("Unlock {calendar}", ["Quick unlock for {calendar}", "Unlock {calendar} for me"]),
    "wrong_passphrase": ("Wrong passphrase", ["That passphrase didn't fit", "Hmm, that's not the one"]),
    "adding": ("Adding to {calendar}…", ["Popping it into {calendar}…", "Adding to {calendar}…"]),
    "opening": ("Opening {calendar}…", ["Opening {calendar} for you…"]),
    "locked": ("Locked", ["Locked up safe", "All locked up"]),
    "unknown_command": ("No command /{name}", ["I don't know /{name}", "No /{name} here"]),
    "restarting": ("Restarting…", ["Back in a moment…"]),
    "quitting": ("Quitting Bamboo", ["Bye for now"]),
    "target_set": ("Default calendar: {calendar}", ["Okay, {calendar} is the default", "{calendar} is your default now"]),
}

FORCE = None        # "plain" or "friendly" overrides the setting (the tests use this); None follows Settings


def enabled():
    if FORCE is not None:
        return FORCE == "friendly"
    return bool(settings.load()["defaults"].get("playful", True))


def _pick(options):
    return random.choice(options)                  # tests replace this to be deterministic


def say(key, **values):
    """The line for `key`, filled in with `values`: friendly if enabled, else the plain wording."""
    plain, friendly = LINES[key]
    text = _pick(friendly) if enabled() else plain
    return text.format(**values)

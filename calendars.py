"""Which calendar an event goes to, and how it gets there. One place for the popup, the commands and
the settings window to ask. Providers are imported only when used, so the idle popup stays small.

A target is "apple" or "google". Both keep their secrets in the passphrase vault, so one unlock covers
both; `needs_unlock()` is the only thing the popup has to check before saving.
"""
import settings
import vault

LABELS = settings.LABELS


def label(target):
    return LABELS.get(target, "a local file")


def choose_target(cfg=None, override=None):
    """The calendar for a new event: a one-off `override` if it is set up, else the default, else None."""
    cfg = cfg or settings.load()
    ready = settings.configured_targets(cfg)
    if override in ready:
        return override
    return settings.default_target(cfg)


def needs_unlock(target):
    """True when saving to `target` would first need the vault passphrase."""
    return target in settings.TARGETS and vault.needs_unlock()


def add_event(target, ev, ics):
    """Save the event. Returns the calendar's name. Raises vault.Locked or RuntimeError (readable text)."""
    if target == "apple":
        import icloud
        return icloud.add_event(ics)
    if target == "google":
        import google_cal
        return google_cal.add_event(ev)
    raise RuntimeError("unknown calendar %r" % target)

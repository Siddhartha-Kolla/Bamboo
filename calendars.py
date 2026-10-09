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


def is_manual(target):
    """True when the event is only opened in the browser and the user still presses Save there."""
    return target == "google" and settings.google_mode() == "link"


def footer_label(target):
    return label(target) + (" (opens in your browser)" if is_manual(target) else "")


def needs_unlock(target):
    """True when saving to `target` would first need the vault passphrase."""
    if is_manual(target):
        return False                                   # the browser link keeps no secret
    return target in settings.TARGETS and vault.needs_unlock()


def add_event(target, ev, ics):
    """Save the event. Returns the calendar's name. Raises vault.Locked or RuntimeError (readable text)."""
    if target == "apple":
        import icloud
        return icloud.add_event(ics)
    if target == "google":
        if is_manual(target):
            import google_link
            return google_link.open_event(ev)
        import google_cal
        return google_cal.add_event(ev)
    raise RuntimeError("unknown calendar %r" % target)

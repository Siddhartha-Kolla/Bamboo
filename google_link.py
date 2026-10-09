"""Google Calendar through its prefilled "add event" web address. No key, no sign-in, no account link.

Pressing Enter opens https://calendar.google.com/calendar/render?action=TEMPLATE&text=...&dates=... in
the user's own browser, with the event already filled in; the user presses Save there. The app never
talks to Google: the browser does, under whatever Google account it is signed in to, and the user picks
the calendar in Google's own form.

Timed events are sent as UTC instants plus `ctz` (the timezone to display them in), so 9:30 in your
timezone shows as 9:30. All-day events use plain dates with an exclusive end day, as Google expects.
"""
import datetime as dt
import urllib.parse
import webbrowser

import eventout

BASE = "https://calendar.google.com/calendar/render"
MAX_TITLE = 300                                    # keeps the address comfortably short
_open = webbrowser.open                            # tests swap this for a recorder


def _utc(value, zone):
    """UTC instant for a naive wall-clock time in `zone` (None = the PC's own timezone)."""
    stamp = value.replace(tzinfo=zone) if zone is not None else value
    return stamp.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_url(ev):
    """The prefilled-event address for a parsed event (see eventparser.parse)."""
    params = [("action", "TEMPLATE"), ("text", ev["title"][:MAX_TITLE])]
    if ev["all_day"]:
        last_day = ev["end"] + dt.timedelta(days=1)                       # Google's end date is exclusive
        params.append(("dates", "%s/%s" % (ev["start"].strftime("%Y%m%d"), last_day.strftime("%Y%m%d"))))
    else:
        found = eventout.local_zone()
        zone = found[1] if found else None
        params.append(("dates", "%s/%s" % (_utc(ev["start"], zone), _utc(ev["end"], zone))))
        if found:
            params.append(("ctz", found[0]))
    if ev.get("rrule"):
        params.append(("recur", "RRULE:" + ev["rrule"]))
    return BASE + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def open_event(ev):
    """Open the prefilled form in the browser. Raises RuntimeError if no browser could be started."""
    if not _open(build_url(ev)):
        raise RuntimeError("could not open your browser")
    return "your browser"

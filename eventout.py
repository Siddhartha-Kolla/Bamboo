"""Turn a parsed event (see eventparser.parse) into calendar-file text, and save a local .ics file.

Timed events carry the PC's timezone (TZID + VTIMEZONE), so 9:30 is 9:30 where you are, and a weekly
event keeps its clock time across daylight saving. Without a timezone calendars guess (Notion Calendar
read the bare time as UTC and showed 9:30 as 11:30). If no zone can be found the times are written in
UTC, which is still the right moment.
"""
import datetime as dt
import re
import uuid
from pathlib import Path

import paths
import settings

OUT_DIR = paths.OUT_DIR


def local_zone():
    """(IANA name, ZoneInfo) for the timezone setting ("auto" = the PC's zone right now), or None."""
    try:
        from zoneinfo import ZoneInfo
        wanted = settings.load()["defaults"]["timezone"]
        if wanted and wanted != "auto":
            return wanted, ZoneInfo(wanted)
        import tzlocal
        name = tzlocal.get_localzone_name()
        return name, ZoneInfo(name)
    except Exception:  # noqa: BLE001 - any failure just means "fall back to UTC"
        return None


def _vtimezone(name, zone, ev):
    """A VTIMEZONE block covering the event (and, for repeating events, many years after it)."""
    import icalendar
    last_year = ev["end"].year + (10 if ev.get("rrule") else 1)
    vtz = icalendar.Timezone.from_tzinfo(zone, tzid=name, first_date=dt.date(ev["start"].year - 1, 1, 1),
                                         last_date=dt.date(last_year, 12, 31))
    return vtz.to_ical().decode("utf-8").strip().split("\r\n")


def build_ics(ev):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    zone_lines = []
    if ev["all_day"]:
        # an all-day DTEND is exclusive, so the last day shown to the user is one day before it
        when = "DTSTART;VALUE=DATE:%s\r\nDTEND;VALUE=DATE:%s" % (
            ev["start"].strftime("%Y%m%d"), (ev["end"] + dt.timedelta(days=1)).strftime("%Y%m%d"))
    else:
        found = local_zone()
        fmt = "%Y%m%dT%H%M%S"
        if found:
            name, zone = found
            zone_lines = _vtimezone(name, zone, ev)
            when = "DTSTART;TZID=%s:%s\r\nDTEND;TZID=%s:%s" % (name, ev["start"].strftime(fmt), name, ev["end"].strftime(fmt))
        else:
            as_utc = lambda value: value.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: E731 - naive = PC local time
            when = "DTSTART:%s\r\nDTEND:%s" % (as_utc(ev["start"]), as_utc(ev["end"]))
    title = ev["title"].replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    lines = (["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//bamboo//EN"] + zone_lines
             + ["BEGIN:VEVENT", "UID:%s@bamboo" % uuid.uuid4(), "DTSTAMP:" + stamp, when, "SUMMARY:" + title])
    if ev.get("rrule"):
        lines.append("RRULE:" + ev["rrule"])
    return "\r\n".join(lines + ["END:VEVENT", "END:VCALENDAR", ""])


def write_ics(ev):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^\w\- ]", "", ev["title"])[:40].strip() or "event"
    path = OUT_DIR / ("%s_%s.ics" % (dt.datetime.now().strftime("%Y%m%d-%H%M%S"), name))
    path.write_text(build_ics(ev), encoding="utf-8", newline="")
    return path

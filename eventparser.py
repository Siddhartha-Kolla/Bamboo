"""Natural-language event parser: "dentist with Dr Weber friday 3pm" -> title, start, end, repeat.

ctparse is good at resolving a *date phrase* ("friday 3pm", "12 march at 7pm") but unreliable when
handed a whole sentence: it reads "mom" as Monday and clips "Sam" to "am". So this module finds
candidate date/time phrases on word boundaries itself, asks ctparse to resolve only those, and
accepts a result only when ctparse used the whole phrase. Everything else becomes the title.

Things ctparse cannot do are done here first, on a same-length copy of the text:
  - whole periods: "next week", "this weekend", "next month", "friday next week"
  - a repeat rule: "every monday", "daily"
  - a length: "for 90 minutes", or just "9:30 180 minutes"
Then two date/time phrases joined by "until / to / - / through" become a start and an end, which can
fall on different days ("today until 31 october", "tomorrow 9 until 31 october 12pm").

    parse(text, now=None) -> None | {
        "title": str,
        "start": datetime | date,     # a date means an all-day event
        "end": datetime | date,       # timed: the end moment; all-day: the LAST day (inclusive)
        "all_day": bool,
        "minutes": int,               # length of a timed event, 0 if all-day
        "rrule": str | None,          # iCalendar RRULE body, e.g. "FREQ=WEEKLY;BYDAY=MO"
        "hints": [str, ...],          # assumptions made, for the UI to show
    }
"""
import calendar
import datetime as dt
import logging
import re

DEFAULT_MINUTES = 60
DEFAULT_START_HOUR = 9      # used when only the end of a range has a time

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ABBR = {"mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun"}
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
          "sept", "oct", "nov", "dec"}
UNITS = {"hour", "hours", "hr", "hrs", "minute", "minutes", "min", "mins", "day", "days", "week", "weeks",
         "month", "months"}
KEYWORDS = {"today", "tomorrow", "tmrw", "tonight", "noon", "midnight", "morning", "afternoon", "evening",
            "night", "am", "pm", "next", "this", "coming", "weekend"} | UNITS
NUMWORDS = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
            "twelve", "half", "an", "a"}
SEPARATORS = {"to", "until", "till", "through", "thru", "-", "–"}
SEP_GAP = SEPARATORS | {"until the", "till the", "to the", "through the"}
CONNECTORS = {"at", "on", "in", "from", "by", "around", "the", "of", "and", "after", "before"} | SEPARATORS
EDGE_WORDS = CONNECTORS | {"this", "next", "coming"}       # may sit just outside what ctparse matched
BARE_NUMBER_AFTER = {"at", "from", "by", "around", "in"} | SEPARATORS
CORE_WORDS = set(DAYS) | DAY_ABBR | MONTHS | KEYWORDS | NUMWORDS

TIMEISH = re.compile(r"^\d{1,2}(:\d{2})?(am|pm)?(-\d{1,2}(:\d{2})?(am|pm)?)?$|^\d{1,4}[/.\-]\d{1,2}([/.\-]\d{1,4})?$|^\d{1,2}(st|nd|rd|th)$")
BARE_INT = re.compile(r"^\d{1,2}$")
HAS_AMPM = re.compile(r"\d\s*(am|pm)\b|\b(am|pm)\b", re.I)

WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4}
_QTY = r"(\d+(?:\.\d+)?|half\s+an?|an?|one|two|three|four)"
FOR_DURATION = re.compile(r"\b(?:for|lasting)\s+" + _QTY + r"\s*(hours?|hrs?|h|minutes?|mins?|m)\b", re.I)
AFTER_TIME_DURATION = re.compile(r"\b" + _QTY + r"\s*(hours?|hrs?|h|minutes?|mins?)\b", re.I)
CLOCK_BEFORE = re.compile(r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)?|noon|midnight)\s*$", re.I)
REPEAT = re.compile(r"\bevery\s+(day|weekday|week|month|%s)\b|\b(daily|weekly|monthly)\b" % "|".join(DAYS), re.I)
BYDAY = dict(zip(DAYS, ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]))
PERIOD = re.compile(r"\b(next|this|coming|following)\s+(week|month|weekend)\b", re.I)
DAY_WORD = re.compile(r"\b(%s)\b" % "|".join(DAYS), re.I)
LONE_POD_WORDS = {"morning", "afternoon", "evening", "night", "the", "in", "at", "on", "of", "a"}
POD_HOUR = {"morning": 9, "noon": 12, "afternoon": 14, "evening": 18, "night": 20, "midnight": 0}

LEAD_FILLER = re.compile(r"^(?:please\s+|can you\s+|could you\s+)?(?:remind me to|remind me about|remind me|add|schedule|"
                         r"set up|book|create(?: an?)?(?: new)?(?: event| meeting| appointment| reminder)?"
                         r"(?: called| titled| for)?|new event)\b[\s:,\-]*", re.I)
EDGE_TRIM = {"at", "on", "in", "from", "to", "until", "till", "for", "by", "around", "with", "and", "of", "the", "a", "an"}

_ctparse = None


def _load():
    global _ctparse
    if _ctparse is None:
        import ctparse
        logging.getLogger("ctparse").setLevel(logging.CRITICAL)   # silences "Failed to produce result"
        _ctparse = ctparse
    return _ctparse


def _norm(word):
    return word.lower().strip(",.;!?()[]\"'")


def _tokens(text):
    # the end offset leaves trailing punctuation out, so cutting a phrase keeps "3pm, bring" a comma
    return [(m.start(), m.start() + len(m.group().rstrip(",.;!?)]\"'")), _norm(m.group()))
            for m in re.finditer(r"\S+", text)]


def _is_core(toks, i):
    w = toks[i][2]
    if not w:
        return False
    if TIMEISH.match(w) and not BARE_INT.match(w):
        return True
    if w in CORE_WORDS:
        return w not in {"a", "an"} or (i + 1 < len(toks) and toks[i + 1][2] in UNITS)
    if BARE_INT.match(w):
        prev = toks[i - 1][2] if i else ""
        nxt = toks[i + 1][2] if i + 1 < len(toks) else ""
        return (prev in BARE_NUMBER_AFTER or prev in MONTHS or nxt in MONTHS or nxt in SEPARATORS
                or nxt in ("am", "pm") or nxt in UNITS)
    return False


def _runs(toks):
    """Index ranges of consecutive date-ish tokens (core words and connectors), each holding a core word."""
    runs, cur, has_core = [], [], False
    for i in range(len(toks)):
        core = _is_core(toks, i)
        if core or toks[i][2] in CONNECTORS:
            cur.append(i)
            has_core = has_core or core
        else:
            if cur and has_core:
                runs.append(cur)
            cur, has_core = [], False
    if cur and has_core:
        runs.append(cur)
    return runs


def _resolve(phrase, now):
    """ctparse result for `phrase` if it used all of it, else None."""
    ct = _load()
    r = ct.ctparse(phrase, ts=now, latent_time=False, timeout=0.5)
    if r is None:
        return None
    res = r.resolution
    kind = type(res).__name__
    if kind not in ("Time", "Interval", "Duration"):
        return None
    edges = _norm(phrase[:res.mstart]).split() + _norm(phrase[res.mend:]).split()
    if any(w not in EDGE_WORDS for w in edges):
        return None
    if kind == "Interval" and (res.isDateInterval or not res.isTimeInterval):
        return None                       # multi-day spans are built by this module, not ctparse
    if kind == "Duration" and "in" not in _norm(phrase[:res.mstart]).split():
        return None                       # "2 hours" alone is a length, only "in 2 hours" is a time
    if kind == "Time" and not (res.hasDate or res.hasTime or res.hasPOD):
        return None
    if kind == "Time" and {_norm(w) for w in phrase.split()} <= LONE_POD_WORDS:
        return None                       # ctparse dates a lone "night" as today, but it is a title word ("night shift")
    return res


def _trim_separators(text, a, b):
    """Drop a leading/trailing "to", "until", "-" ... so the separator stays out of the phrase."""
    while True:
        words = list(re.finditer(r"\S+", text[a:b]))
        if len(words) > 1 and _norm(words[0].group()) in SEPARATORS:
            a += words[1].start()
        elif len(words) > 1 and _norm(words[-1].group()) in SEPARATORS:
            b = a + words[-2].end()
        else:
            return a, b


def _find(text, toks, now, lo, hi, out):
    """Accept the longest resolvable phrase inside toks[lo..hi], then search what is left on each side."""
    if lo > hi:
        return
    for length in range(hi - lo + 1, 0, -1):
        for s in range(lo, hi - length + 2):
            e = s + length - 1
            if not any(_is_core(toks, k) for k in range(s, e + 1)):
                continue
            a, b = toks[s][0], toks[e][1]
            res = _resolve(text[a:b], now)
            if res is not None:
                a2, b2 = _trim_separators(text, a, b)
                out.append((a2, b2, res, text[a2:b2]))
                _find(text, toks, now, lo, s - 1, out)
                _find(text, toks, now, e + 1, hi, out)
                return


def _hour24(hour, explicit_ampm, hints):
    if not explicit_ampm and 1 <= hour <= 7:
        hints.append("assumed %d pm" % hour)
        return hour + 12
    return hour


def _date_from(res, now):
    if res.day is None or res.month is None:
        return None
    year = res.year
    if year is None:
        year = now.year
        if dt.date(year, res.month, res.day) < now.date():
            year += 1
    return dt.date(year, res.month, res.day)


def _facts(res, phrase, now):
    return {"date": _date_from(res, now), "hour": res.hour, "minute": res.minute or 0,
            "pod": res.POD, "ampm": bool(HAS_AMPM.search(phrase))}


def _clock(f, hints):
    """(hour, minute) for one side of a range, or None when it names no time of day."""
    if f["hour"] is not None:
        return _hour24(f["hour"], f["ampm"], hints), f["minute"]
    if f["pod"]:
        hints.append("%s taken as %02d:00" % (f["pod"], POD_HOUR[f["pod"]]))
        return POD_HOUR[f["pod"]], 0
    return None


def _next_day_for(hour, minute, now):
    return now.date() if dt.time(hour, minute) > now.time() else now.date() + dt.timedelta(days=1)


def _range(left, right, other_date, now, hints):
    """Start and end from two phrases joined by until/to/-. Returns (start, end, all_day)."""
    lc, rc = _clock(left, hints), _clock(right, hints)
    ld = left["date"] or other_date
    rd = right["date"]
    if lc is None and rc is None and ld and rd:
        if rd < ld:
            hints.append("end is before start, kept one day")
            rd = ld
        return ld, rd, True

    if lc is not None and rc is not None and not left["ampm"] and right["ampm"]:
        lh, rh = lc[0], rc[0]                           # "9 until 11pm" / "3-4pm": the pm covers both ends
        if lh < 12 <= rh and lh + 12 < rh:
            lc = (lh + 12, lc[1])
    if lc is None:
        lc = (DEFAULT_START_HOUR, 0)
        hints.append("start taken as %02d:00" % DEFAULT_START_HOUR)
    if ld is None:
        ld = _next_day_for(lc[0], lc[1], now)
    start = dt.datetime.combine(ld, dt.time(*lc))
    if rc is None:
        rc = (23, 59)
        hints.append("ends at the end of that day")
    end_date = rd or ld
    end = dt.datetime.combine(end_date, dt.time(*rc))
    if end <= start:
        if rd is None:
            end += dt.timedelta(days=1)
            hints.append("ends the next day")
        else:
            end = start + dt.timedelta(minutes=DEFAULT_MINUTES)
            hints.append("end is before start, kept 1 h")
    return start, end, False


def _period(match, work, today, cuts, blank):
    """(first_day, last_day) for "next week", "this weekend", "next month", "friday next week" ..."""
    which = match.group(1).lower()
    unit = match.group(2).lower()
    later = which != "this"
    monday = today - dt.timedelta(days=today.weekday())
    blank(match.start(), match.end())
    cuts.append((match.start(), match.end()))
    if unit == "week":
        base = monday + dt.timedelta(days=7 if later else 0)
        dm = DAY_WORD.search("".join(work))
        if dm:                                           # "friday next week" names one day of it
            blank(dm.start(), dm.end())
            cuts.append((dm.start(), dm.end()))
            day = base + dt.timedelta(days=DAYS.index(dm.group(1).lower()))
            return day, day
        return (base if later else today), base + dt.timedelta(days=6)
    if unit == "weekend":
        sat = monday + dt.timedelta(days=5 + (7 if later else 0))
        if not later and today.weekday() >= 5:
            sat = today
        return sat, sat + dt.timedelta(days=1 if sat.weekday() == 5 else 0)
    year, month = today.year, today.month
    if later:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    first = dt.date(year, month, 1) if later else today
    return first, dt.date(year, month, calendar.monthrange(year, month)[1])


def _clean_title(text, cuts):
    parts, pos = [], 0
    for a, b in sorted(cuts):
        parts.append(text[pos:a])
        pos = max(pos, b)
    parts.append(text[pos:])
    title = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s+", " ", " ".join(parts))).strip(" ,.;:-")
    prev = None
    while prev != title:                                   # a few fillers can stack up
        prev = title
        title = LEAD_FILLER.sub("", title).strip(" ,.;:-")
    words = title.split()
    while words and words[0].lower() in EDGE_TRIM - {"the", "a", "an", "with", "and", "of"}:
        words.pop(0)
    while words and words[-1].lower() in EDGE_TRIM:
        words.pop()
    title = " ".join(words)
    return title[:1].upper() + title[1:] if title else "(no title)"


def _qty(raw):
    raw = raw.lower()
    return 0.5 if raw.startswith("half") else float(WORD_NUM.get(raw) or raw)


def parse(text, now=None):
    text = text.strip()
    if not text:
        return None
    now = (now or dt.datetime.now()).replace(second=0, microsecond=0)
    today = now.date()
    hints, cuts, minutes_given, rrule, period = [], [], None, None, None
    work = list(text)

    def blank(a, b):
        work[a:b] = [" "] * (b - a)

    m = REPEAT.search(text)
    if m:
        if m.group(1):
            unit = m.group(1).lower()
            if unit in BYDAY:
                rrule = "FREQ=WEEKLY;BYDAY=" + BYDAY[unit]
                cuts.append((m.start(), m.start() + len("every")))   # keep the weekday: it dates the first event
            else:
                rrule = {"day": "FREQ=DAILY", "weekday": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
                         "week": "FREQ=WEEKLY", "month": "FREQ=MONTHLY"}[unit]
                cuts.append((m.start(), m.end()))
                blank(m.start(), m.end())
        else:
            rrule = {"daily": "FREQ=DAILY", "weekly": "FREQ=WEEKLY", "monthly": "FREQ=MONTHLY"}[m.group(2).lower()]
            cuts.append((m.start(), m.end()))
            blank(m.start(), m.end())

    m = PERIOD.search("".join(work))
    if m:
        period = _period(m, work, today, cuts, blank)

    s = "".join(work)
    m = FOR_DURATION.search(s)
    if m:
        unit = m.group(2).lower()
        minutes_given = int(round(_qty(m.group(1)) * (60 if unit.startswith("h") else 1)))
    else:
        for cand in AFTER_TIME_DURATION.finditer(s):
            before = s[:cand.start()].rstrip()
            if before.lower().endswith(" in") or before.lower() == "in":
                continue                                   # "in 2 hours" is a start time, ctparse reads it
            if CLOCK_BEFORE.search(before):
                m = cand
                unit = cand.group(2).lower()
                minutes_given = int(round(_qty(cand.group(1)) * (60 if unit.startswith("h") else 1)))
                break
    if m and minutes_given is not None:
        cuts.append((m.start(), m.end()))
        blank(m.start(), m.end())

    s = "".join(work)
    toks = _tokens(s)
    found = []
    for run in _runs(toks):
        _find(s, toks, now, run[0], run[-1], found)
    found.sort(key=lambda f: f[0])
    cuts.extend((a, b) for a, b, _r, _t in found)

    times = [(a, b, _facts(res, ph, now)) for a, b, res, ph in found if type(res).__name__ == "Time"]
    interval = next(((res.t_from, res.t_to, bool(HAS_AMPM.search(ph))) for _a, _b, res, ph in found
                     if type(res).__name__ == "Interval"), None)
    offset = None
    for _a, _b, res, _ph in found:
        if type(res).__name__ == "Duration":
            offset = {"MINUTES": dt.timedelta(minutes=res.value), "HOURS": dt.timedelta(hours=res.value),
                      "DAYS": dt.timedelta(days=res.value), "WEEKS": dt.timedelta(weeks=res.value)}.get(res.unit.name)

    pair = None
    for i in range(len(times) - 1):
        if s[times[i][1]:times[i + 1][0]].strip().lower() in SEP_GAP:
            pair = (i, i + 1)
            break

    start = end = None
    all_day = False
    if offset is not None:
        start = now + offset
        if offset >= dt.timedelta(days=1):
            start, all_day = start.date(), True
    elif pair is not None:
        others = [t[2] for k, t in enumerate(times) if k not in pair]
        other_date = next((o["date"] for o in others if o["date"]), None) or (period[0] if period and period[0] == period[1] else None)
        start, end, all_day = _range(times[pair[0]][2], times[pair[1]][2], other_date, now, hints)
    elif interval is not None and interval[0].hour is not None and interval[1].hour is not None:
        f, t, ampm = interval
        fh, th = f.hour, t.hour
        if not ampm:
            fh, th = _hour24(fh, False, hints), _hour24(th, False, hints)
        elif fh < 12 <= th and fh + 12 < th:
            fh += 12                                     # "3-4pm": the pm belongs to both ends
        date = next((x[2]["date"] for x in times if x[2]["date"]), None) or (period[0] if period and period[0] == period[1] else None)
        day = date or _next_day_for(fh, f.minute or 0, now)
        start = dt.datetime.combine(day, dt.time(fh, f.minute or 0))
        end = dt.datetime.combine(day, dt.time(th, t.minute or 0))
        if end <= start:
            end += dt.timedelta(days=1)
    else:
        date, hour, minute, pod, explicit = None, None, 0, None, False
        for _a, _b, f in times:
            date = f["date"] or date
            if f["hour"] is not None:
                hour, minute, explicit = f["hour"], f["minute"], f["ampm"]
            elif f["pod"]:
                pod = f["pod"]
        if period and (period[0] != period[1]) and date is None:
            start, end, all_day = period[0], period[1], True
            if hour is not None or pod is not None:
                hints.append("a whole %s has no start time, time ignored" % ("period"))
        else:
            if date is None and period:
                date = period[0]
            if hour is None and pod is not None:
                hour, minute = POD_HOUR[pod], 0
                hints.append("%s taken as %02d:00" % (pod, hour))
            elif hour is not None:
                hour = _hour24(hour, explicit, hints)
            if hour is None and date is not None:
                start, all_day = date, True
            elif hour is not None:
                day = date or _next_day_for(hour, minute, now)
                start = dt.datetime.combine(day, dt.time(hour, minute))
            else:
                start, all_day = today, True
                hints.append("no date found, using today")

    if rrule and "BYDAY=MO,TU,WE,TH,FR" in rrule:
        day = start.date() if isinstance(start, dt.datetime) else start
        skip = {5: 2, 6: 1}.get(day.weekday(), 0)
        if skip:
            start += dt.timedelta(days=skip)
            if end is not None:
                end += dt.timedelta(days=skip)
            hints.append("weekdays only, first one is %s" % (day + dt.timedelta(days=skip)).strftime("%A"))

    if all_day:
        end = end or start
        minutes = 0
    else:
        if end is None:
            end = start + dt.timedelta(minutes=minutes_given or DEFAULT_MINUTES)
        minutes = int((end - start).total_seconds() // 60)

    return {"title": _clean_title(text, cuts), "start": start, "end": end, "all_day": all_day,
            "minutes": minutes, "rrule": rrule, "hints": hints}

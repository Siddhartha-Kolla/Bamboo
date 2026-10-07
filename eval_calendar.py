"""Score parser.parse against test_events.json.   python eval_calendar.py [--verbose]"""
import datetime as dt
import json
import sys
import time
from pathlib import Path

from eventparser import parse

HERE = Path(__file__).parent


def iso(value):
    return value.isoformat(timespec="minutes") if isinstance(value, dt.datetime) else value.isoformat()


def main():
    verbose = "--verbose" in sys.argv
    data = json.loads((HERE / "test_events.json").read_text(encoding="utf-8"))
    now = dt.datetime.fromisoformat(data["now"])
    passed, slowest = 0, 0.0
    for case in data["cases"]:
        t0 = time.perf_counter()
        ev = parse(case["text"], now)
        slowest = max(slowest, time.perf_counter() - t0)
        problems = []
        if ev is None:
            problems.append("no result")
        else:
            if ev["title"] != case["title"]:
                problems.append("title %r != %r" % (ev["title"], case["title"]))
            if iso(ev["start"]) != case["start"]:
                problems.append("start %s != %s" % (iso(ev["start"]), case["start"]))
            if "end" in case and iso(ev["end"]) != case["end"]:
                problems.append("end %s != %s" % (iso(ev["end"]), case["end"]))
            if ev["all_day"] != case.get("all_day", False):
                problems.append("all_day %s" % ev["all_day"])
            if "minutes" in case and ev["minutes"] != case["minutes"]:
                problems.append("minutes %s != %s" % (ev["minutes"], case["minutes"]))
            if ev["rrule"] != case.get("rrule"):
                problems.append("rrule %s != %s" % (ev["rrule"], case.get("rrule")))
        if not problems:
            passed += 1
        if problems or verbose:
            print("%-4s %-46s %s" % ("ok" if not problems else "FAIL", case["text"], "; ".join(problems)))
    print("\n%d/%d passed   (slowest parse %.0f ms)" % (passed, len(data["cases"]), slowest * 1000))


main()

"""Score app_opener.detect against test_cases.json.

Usage:  python eval_apps.py [--verbose]

Never launches anything. Needs the Laya server running for phrases the alias matcher misses.
"""
import json
import sys
from pathlib import Path

from app_opener import detect, load_key

HERE = Path(__file__).parent


def main():
    verbose = "--verbose" in sys.argv
    key = load_key()
    cases = json.loads((HERE / "test_cases.json").read_text(encoding="utf-8"))

    rows = []
    for case in cases:
        got, how = detect(case["text"], key)
        expect = case["expect"]
        ok = sorted(got) == sorted(expect)
        kind = "multi" if case.get("multi") else (expect[0] if expect else "negative")
        rows.append((kind, ok))
        if not ok or verbose:
            if isinstance(how, str):
                detail = how
            else:
                detail = " ".join("%s=%.2f" % (n, p) for n, p in how.items() if p >= 0.2)
            print("%-4s %-36s expect=%-22s got=%-22s %s"
                  % ("ok" if ok else "FAIL", case["text"], expect or "-", got or "-", detail))

    print("\nper group:")
    for kind in sorted({k for k, _ in rows}):
        group = [ok for k, ok in rows if k == kind]
        print("  %-9s %d/%d" % (kind, sum(group), len(group)))
    total = sum(ok for _, ok in rows)
    print("overall: %d/%d (%.0f%%)" % (total, len(rows), 100.0 * total / len(rows)))


main()

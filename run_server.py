"""Start the Laya HTTP server, reading config from .env next to this file.

Laya itself only reads real environment variables, so this loads .env first.
Variables already set in the environment win over .env.
"""
import os
from pathlib import Path


def load_env(path):
    if not path.exists():
        raise SystemExit("Missing %s - copy .env.example to .env first." % path)
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env(Path(__file__).parent / ".env")
if not os.environ.get("LAYA_API_KEY"):
    raise SystemExit("LAYA_API_KEY is empty in .env - refusing to start an open server.")

from laya.serve import main

main()

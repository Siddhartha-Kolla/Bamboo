"""Open apps from natural language: alias match first, Laya (one yes/no question per app) as fallback.

    python app_opener.py        # interactive
    from app_opener import detect, launch
"""
import os
import re
import subprocess
from pathlib import Path

import requests

URL = os.environ.get("LAYA_URL", "http://127.0.0.1:8000") + "/v1/systemone"
THRESHOLD = 0.6  # probability the best app needs when the model alone decides
MARGIN = 0.1     # and how far it must beat the second-best app

# label/desc feed the model; aliases feed the exact matcher. cmd is the only thing ever executed.
APPS = {
    "chrome":  {"label": "Google Chrome", "cmd": "start chrome",
                "aliases": ["chrome", "google chrome", "browser", "web browser"],
                "desc": "web browser for websites and the internet"},
    "spotify": {"label": "Spotify", "cmd": "start spotify:",
                "aliases": ["spotify", "music app", "music player"],
                "desc": "music streaming app for songs and playlists"},
    "notepad": {"label": "Notepad", "cmd": "notepad",
                "aliases": ["notepad", "text editor", "note pad"],
                "desc": "plain text editor for jotting down quick notes"},
    "vscode":  {"label": "Visual Studio Code", "cmd": "code",
                "aliases": ["vscode", "vs code", "visual studio code", "code editor", "ide"],
                "desc": "code editor for writing programs"},
    "calc":    {"label": "Calculator", "cmd": "calc",
                "aliases": ["calculator", "calc"],
                "desc": "calculator for doing math"},
}

OPEN_VERBS = re.compile(r"\b(open|launch|start|run|fire up|bring up|load|boot)\b", re.I)

QUESTIONS = {
    "open_" + name: {"type": "noul",
        "instructions": "Does the user's message ask to open or start %s, the %s?" % (a["label"], a["desc"])}
    for name, a in APPS.items()
}


def load_key():
    key = os.environ.get("LAYA_API_KEY")
    if key:
        return key
    for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("LAYA_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("LAYA_API_KEY not found in environment or .env")


def match_aliases(text):
    """Apps named outright, but only when the text also contains an open-style verb."""
    if not OPEN_VERBS.search(text):
        return []
    return [name for name, a in APPS.items()
            if any(re.search(r"\b%s\b" % re.escape(al), text, re.I) for al in a["aliases"])]


def mentions_app(text):
    return any(re.search(r"\b%s\b" % re.escape(al), text, re.I)
               for a in APPS.values() for al in a["aliases"])


def ask_model(text, key):
    r = requests.post(URL, json={"state": text, "questions": QUESTIONS},
                      headers={"Authorization": "Bearer " + key}, timeout=60)
    r.raise_for_status()
    answers = r.json()["answers"]
    return {name: answers["open_" + name]["noul"] for name in APPS}


def detect(text, key=None):
    """Return (apps_to_open, how). Never launches anything."""
    hits = match_aliases(text)
    if hits:
        return hits, "alias"
    if mentions_app(text) and len(text.split()) > 3:
        return [], "statement"  # names an app without asking to open it ("notepad was my first editor")
    scores = ask_model(text, key or load_key())
    # Alias matching already covers multi-app requests, so the model path opens at most one app:
    # the best score, and only when it clears the threshold and clearly beats the runner-up.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, runner_up = ranked[0], ranked[1]
    if best[1] >= THRESHOLD and best[1] - runner_up[1] >= MARGIN:
        return [best[0]], scores
    return [], scores


def launch(apps):
    for name in apps:
        subprocess.Popen(APPS[name]["cmd"], shell=True)  # cmd comes from APPS, never from the text


if __name__ == "__main__":
    while True:
        text = input("Ask (or 'exit'): ")
        if text.lower() == "exit":
            break
        apps, how = detect(text)
        print("->", apps or "nothing to open", "(alias)" if how == "alias" else "")
        launch(apps)

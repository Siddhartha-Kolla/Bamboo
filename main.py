import json
import subprocess
import requests

from app_opener import load_key   # reads LAYA_API_KEY from the environment or .env


APPS = {
    "chrome":   {"desc": "web browser, websites, internet, google", "cmd": "start chrome"},
    "spotify":  {"desc": "music, songs, playlists",                  "cmd": "start spotify:"},
    "notepad":  {"desc": "quick text notes, simple text editor, open notepad",     "cmd": "notepad"},
    "vscode":   {"desc": "code editor, programming, IDE",            "cmd": "code"},
    "calc":     {"desc": "calculator, arithmetic",                   "cmd": "calc"},
    "none":     {"desc": "no matching app in this list",             "cmd": None},
}


QUESTIONS = {
    "is_open_request": {"type": "noul",
        "instructions": "Is the user asking to open or launch an application?"},
    "app": {"type": "choice",
        "instructions": "Which application does the user want to open?",
        "criteria": {name: a["desc"] for name, a in APPS.items()}},
}

def handle(text,answers):
    # print(answers)
    print(answers["is_open_request"]["noul"])
    if answers["is_open_request"]["noul"] < 0.5:
        return "Not an open-app request."
    app = answers["app"]["choice"]
    cmd = APPS.get(app, {}).get("cmd")   # unknown key -> nothing runs
    if not cmd:
        return "No matching app."
    subprocess.Popen(cmd, shell=True)
    return "Opened " + app




while True:
    question = input("Ask a question (or type 'exit' to quit): ")
    if question.lower() == 'exit':
        break

    response = requests.post(
        "http://127.0.0.1:8000/v1/systemone",
        headers={
            "Authorization": "Bearer " + load_key(),
            "Content-Type": "application/json"
        },
        data=json.dumps({
            "state": question,
            "questions": QUESTIONS
        })
    )
    responst_json = response.json()
    handle_result = handle(question, responst_json["answers"])
    print(handle_result)

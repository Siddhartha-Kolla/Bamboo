"""Control the Bamboo popup:   bamboo start | stop | restart | status | autostart on|off|status | log

    bamboo restart        stop the running popup (if any) and start a fresh one, which loads your code changes
    bamboo autostart on   start the popup automatically when you log in to Windows

`bamboo` is bamboo.bat in this folder. Everything here uses the project's own .venv.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import paths
import singleton

HERE = Path(__file__).resolve().parent
PYTHONW = HERE / ".venv" / "Scripts" / "pythonw.exe"
SCRIPT = HERE / "bamboo.py"
ICON = HERE / "assets" / "bamboo.ico"
LOG = paths.LOG
PIDFILE = paths.PIDFILE
STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
_DETACHED = 0x00000008 | 0x00000200 | 0x08000000      # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW


def _wait(condition, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def start():
    if singleton.is_running():
        print("Already running.")
        return True
    if not PYTHONW.exists():
        print("Cannot find %s - is the .venv here?" % PYTHONW)
        return False
    subprocess.Popen([str(PYTHONW), str(SCRIPT)], cwd=str(HERE), creationflags=_DETACHED,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if _wait(singleton.is_running, 8):
        print("Started. Press Ctrl+Alt+C, or use the tray icon.")
        return True
    print("It did not come up within 8 seconds. See the log:  %s" % LOG)
    return False


def _kill_leftover():
    """Last resort: end the process in the PID file, but only if it really is bamboo.py (or its old name)."""
    try:
        pid = int(PIDFILE.read_text().strip())
    except (OSError, ValueError):
        return False
    check = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine" % pid],
        capture_output=True, text=True)
    if not any(script in (check.stdout or "") for script in ("bamboo.py", "quickadd.py")):
        return False
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    return True


def stop():
    if not singleton.is_running():
        print("Not running.")
        return True
    singleton.request_stop()
    if _wait(lambda: not singleton.is_running(), 5):
        print("Stopped.")
        return True
    print("It did not quit by itself, ending it ...")
    _kill_leftover()
    if _wait(lambda: not singleton.is_running(), 3):
        print("Stopped.")
        return True
    print("Still running. Close it from Task Manager (look for pythonw.exe).")
    return False


def restart():
    return stop() and start()


def status():
    if singleton.is_running():
        pid = PIDFILE.read_text().strip() if PIDFILE.exists() else "?"
        print("Running (pid %s)." % pid)
    else:
        print("Not running.")
    print("Starts with Windows: %s" % ("yes" if autostart_enabled() else "no"))


def startup_links():
    """(current shortcut, shortcut made before the rename)."""
    return STARTUP_DIR / "Bamboo.lnk", STARTUP_DIR / "Quickadd.lnk"


def autostart_enabled():
    new, old = startup_links()
    return new.exists() or old.exists()


def _write_shortcut(path):
    icon = "$s.IconLocation = '%s'; " % ICON if ICON.exists() else ""
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
          "$s.TargetPath = '%s'; $s.Arguments = '\"%s\"'; $s.WorkingDirectory = '%s'; %s"
          "$s.Description = 'Bamboo calendar'; $s.Save()") % (path, PYTHONW, SCRIPT, HERE, icon)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def set_autostart(on):
    new, old = startup_links()
    old.unlink(missing_ok=True)                        # never leave the old shortcut behind
    if not on:
        new.unlink(missing_ok=True)
        return
    new.parent.mkdir(parents=True, exist_ok=True)
    _write_shortcut(new)


def upgrade_autostart():
    """A shortcut made before the rename points at quickadd.py: replace it by the current one. True if replaced."""
    if startup_links()[1].exists():
        set_autostart(True)
        return True
    return False


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "start":
        return 0 if start() else 1
    if cmd == "stop":
        return 0 if stop() else 1
    if cmd == "restart":
        return 0 if restart() else 1
    if cmd == "status":
        status()
        return 0
    if cmd == "autostart":
        arg = argv[1] if len(argv) > 1 else "status"
        if arg in ("on", "off"):
            set_autostart(arg == "on")
        print("Starts with Windows: %s" % ("yes" if autostart_enabled() else "no"))
        return 0
    if cmd == "log":
        print(LOG.read_text(encoding="utf-8", errors="replace")[-4000:] if LOG.exists() else "(no log yet)")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

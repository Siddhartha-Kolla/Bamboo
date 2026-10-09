"""Windows helpers so only one quick-add popup runs, and so it can be asked to quit cleanly.

The running popup holds a named mutex ("I exist") and a named event ("please quit"). The control
command (quickadd_ctl.py) only needs to look the mutex up and set the event; no PID juggling.
"""
import ctypes
from ctypes import wintypes

MUTEX_NAME = "Local\\quickadd-popup-mutex"
STOP_NAME = "Local\\quickadd-popup-stop"

_ERROR_ALREADY_EXISTS = 183
_EVENT_MODIFY_STATE = 0x0002
_SYNCHRONIZE = 0x00100000

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenMutexW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.SetEvent.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]

_held = []      # handles stay open for the life of the process; Windows frees them when it exits


def acquire():
    """True if this process is now the only popup, False if another one already runs."""
    handle = _k32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        _k32.CloseHandle(handle)
        return False
    _held.append(handle)
    return True


def create_stop_event():
    handle = _k32.CreateEventW(None, True, False, STOP_NAME)    # manual reset, starts unset
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    _held.append(handle)
    return handle


def stop_requested(handle):
    return _k32.WaitForSingleObject(handle, 0) == 0


def is_running():
    handle = _k32.OpenMutexW(_SYNCHRONIZE, False, MUTEX_NAME)
    if handle:
        _k32.CloseHandle(handle)
        return True
    return False


def request_stop():
    """Ask the running popup to quit. False if none is running."""
    handle = _k32.OpenEventW(_EVENT_MODIFY_STATE, False, STOP_NAME)
    if not handle:
        return False
    _k32.SetEvent(handle)
    _k32.CloseHandle(handle)
    return True

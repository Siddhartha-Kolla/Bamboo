"""Backend tests: vault, settings, calendar choice, calendar-file output (and, later, Google and commands).

    python test_backend.py [-v]

Everything runs against temporary files; the real ~/.quickadd.* files are never touched.
"""
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import calendars
import eventout
import settings
import vault

# Safety net: from import time on, nothing in this process can reach the real ~/.quickadd.* files.
_SAFE = Path(tempfile.mkdtemp(prefix="quickadd-test-"))
vault.VAULT, settings.CONFIG, eventout.OUT_DIR = _SAFE / "v.vault", _SAFE / "c.json", _SAFE / "out"

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@contextlib.contextmanager
def isolated():
    """Point vault/settings/output at a temp folder and reset the in-memory unlock state."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        saved = (vault.VAULT, settings.CONFIG, eventout.OUT_DIR, vault.UNLOCK_IDLE_SECONDS)
        vault.VAULT, settings.CONFIG, eventout.OUT_DIR = tmp / "v.vault", tmp / "c.json", tmp / "out"
        vault.lock()
        try:
            yield tmp
        finally:
            vault.lock()
            vault.VAULT, settings.CONFIG, eventout.OUT_DIR, vault.UNLOCK_IDLE_SECONDS = saved


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    except Exception as err:  # noqa: BLE001
        raise AssertionError("expected %s, got %r" % (exc.__name__, err))
    raise AssertionError("expected %s, nothing raised" % exc.__name__)


# ------------------------------------------------------------------------------------------ vault

@test
def vault_roundtrip_and_no_plaintext():
    with isolated():
        vault.create("correct horse battery", {"apple_password": "abcd-efgh-ijkl-mnop"})
        raw = vault.VAULT.read_text()
        assert "abcd-efgh" not in raw and "apple_password" not in raw
        vault.lock()
        assert vault.needs_unlock()
        assert vault.unlock("correct horse battery")
        assert vault.get("apple_password") == "abcd-efgh-ijkl-mnop"


@test
def vault_wrong_passphrase_and_tamper():
    with isolated():
        vault.create("correct horse battery", {"a": "1"})
        vault.lock()
        assert vault.unlock("wrong passphrase!!") is False and not vault.is_unlocked()
        blob = json.loads(vault.VAULT.read_text())
        t = blob["token"]
        blob["token"] = t[:-4] + ("A" if t[-4] != "A" else "B") + t[-3:]
        vault.VAULT.write_text(json.dumps(blob))
        assert vault.unlock("correct horse battery") is False


@test
def vault_put_get_delete_persist():
    with isolated():
        vault.create("pass-phrase-1", {"apple_password": "x"})
        vault.put("google_refresh_token", "r-123")
        vault.delete("apple_password")
        vault.lock()
        assert vault.unlock("pass-phrase-1")
        assert vault.names() == ["google_refresh_token"] and vault.get("google_refresh_token") == "r-123"


@test
def vault_expiry_locks_and_raises():
    with isolated():
        vault.create("pass-phrase-1", {"k": "v"})
        vault._state["until"] = 0.0
        raises(vault.Locked, vault.get, "k")
        assert vault._state["secrets"] is None and vault._state["key"] is None      # references released


@test
def vault_idle_window_is_extended_by_use():
    with isolated():
        vault.UNLOCK_IDLE_SECONDS = 600
        vault.create("pass-phrase-1", {"k": "v"})
        vault._state["until"] = time.monotonic() + 5
        vault.get("k")
        assert vault._state["until"] > time.monotonic() + 500


@test
def vault_short_passphrase_refused():
    with isolated():
        raises(ValueError, vault.create, "short")
        assert not vault.has_vault()


def _make_v1(path, passphrase, secret):
    """A vault exactly as the first version wrote it (one string, v1)."""
    from cryptography.fernet import Fernet
    salt = os.urandom(16)
    raw = hashlib.scrypt(passphrase.encode(), salt=salt, n=2 ** 15, r=8, p=1, dklen=32, maxmem=256 * 1024 * 1024)
    token = Fernet(base64.urlsafe_b64encode(raw)).encrypt(secret.encode())
    blob = {"v": 1, "kdf": "scrypt", "n": 2 ** 15, "r": 8, "p": 1, "salt": base64.b64encode(salt).decode(), "token": token.decode()}
    path.write_text(json.dumps(blob))


@test
def vault_v1_is_migrated_with_backup():
    with isolated():
        _make_v1(vault.VAULT, "old pass phrase", "abcd-efgh-ijkl-mnop")
        original = vault.VAULT.read_text()
        assert vault.unlock("old pass phrase")
        assert vault.get("apple_password") == "abcd-efgh-ijkl-mnop"
        assert json.loads(vault.VAULT.read_text())["v"] == 2
        bak = Path(str(vault.VAULT) + ".bak")
        assert bak.exists() and bak.read_text() == original
        vault.lock()
        assert vault.unlock("old pass phrase") and vault.get("apple_password") == "abcd-efgh-ijkl-mnop"   # reads v2 now
        vault.put("google_refresh_token", "r")
        vault.lock()
        assert vault.unlock("old pass phrase") and vault.names() == ["apple_password", "google_refresh_token"]


# ---------------------------------------------------------------------------------------- settings

@test
def settings_defaults_and_old_file_compat():
    with isolated():
        cfg = settings.load()
        assert cfg["defaults"]["duration_min"] == 60 and not settings.apple_configured(cfg) and settings.default_target(cfg) is None
        settings.CONFIG.write_text(json.dumps({"apple_id": "me@icloud.com", "calendar": "School"}))     # what v1 wrote
        cfg = settings.load()
        assert settings.apple_configured(cfg) and settings.default_target(cfg) == "apple" and cfg["defaults"]["hotkey"] == "ctrl+alt+C"


@test
def settings_bad_values_fall_back():
    with isolated():
        settings.CONFIG.write_text("{ not json")
        assert settings.load()["defaults"] == settings.DEFAULTS
        settings.CONFIG.write_text(json.dumps({"defaults": {"duration_min": -5, "unlock_idle_min": "x", "hotkey": "C", "timezone": ""},
                                               "default_target": "outlook", "google": "nope"}))
        cfg = settings.load()
        assert cfg["defaults"] == settings.DEFAULTS and cfg["default_target"] is None and cfg["google"] == {}


@test
def settings_update_merges_and_keeps_other_keys():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"apple_id": "a", "calendar": "c"}))
        settings.update({"defaults": {"duration_min": 45}})
        settings.update({"google": {"client_id": "x"}})
        settings.update({"google": {"calendar_id": "primary"}})
        cfg = settings.load()
        assert cfg["apple_id"] == "a" and cfg["defaults"]["duration_min"] == 45 and cfg["defaults"]["hotkey"] == "ctrl+alt+C"
        assert cfg["google"] == {"client_id": "x", "calendar_id": "primary"}


@test
def settings_default_target_logic():
    base = {"apple_id": "a", "calendar": "c"}
    google = {"google": {"client_id": "x", "calendar_id": "primary"}}
    cases = [({}, None), (base, "apple"), (google, "google"), ({**base, **google}, "apple"),
             ({**base, **google, "default_target": "google"}, "google"),
             (dict(base, default_target="google"), "apple")]          # chosen default not set up -> first that is
    for raw, want in cases:
        with isolated():
            settings.CONFIG.write_text(json.dumps(raw))
            assert settings.default_target() == want, (raw, want)


@test
def settings_hotkey_validation():
    good = ["ctrl+alt+C", "ctrl+shift+7", "alt+F9", "win+alt+k"]
    bad = ["C", "ctrl", "ctrl+", "ctrl+alt+CC", "ctrl+alt+F13", "shift+C", "ctrl+alt+!", ""]
    assert all(settings.valid_hotkey(h) for h in good), [h for h in good if not settings.valid_hotkey(h)]
    assert not any(settings.valid_hotkey(h) for h in bad), [h for h in bad if settings.valid_hotkey(h)]
    assert settings.parse_hotkey("ctrl+alt+c") == (["ctrl", "alt"], "C") and settings.parse_hotkey("alt+f9") == (["alt"], "F9")


# --------------------------------------------------------------------------------------- calendars

@test
def calendars_choose_target_and_override():
    with isolated():
        both = {"apple_id": "a", "calendar": "c", "google": {"client_id": "x", "calendar_id": "p"}, "default_target": "apple"}
        settings.CONFIG.write_text(json.dumps(both))
        assert calendars.choose_target() == "apple"
        assert calendars.choose_target(override="google") == "google"
        assert calendars.choose_target(override="outlook") == "apple"          # unknown override ignored
        settings.CONFIG.write_text(json.dumps({"apple_id": "a", "calendar": "c"}))
        assert calendars.choose_target(override="google") == "apple"           # google not set up -> default
        settings.CONFIG.write_text("{}")
        assert calendars.choose_target() is None and calendars.label(None) == "a local file"


@test
def calendars_needs_unlock_follows_vault():
    with isolated():
        assert calendars.needs_unlock("apple") is False                       # no vault yet
        vault.create("pass-phrase-1", {"k": "v"})
        assert calendars.needs_unlock("apple") is False                       # unlocked
        vault.lock()
        assert calendars.needs_unlock("apple") and calendars.needs_unlock("google") and not calendars.needs_unlock(None)


# ----------------------------------------------------------------------------------------- eventout

def _ev(text, now=dt.datetime(2026, 10, 7, 9, 0)):
    import eventparser
    return eventparser.parse(text, now)


def _vevent(ics):
    import icalendar
    return list(icalendar.Calendar.from_ical(ics).walk("VEVENT"))[0]


def _utc(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


@test
def ics_timed_event_carries_timezone_summer_and_winter():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Europe/Berlin"}}))
        e = _vevent(eventout.build_ics(_ev("meeting friday 9:30")))
        assert _utc(e["DTSTART"].dt) == "2026-10-09 07:30"                    # CEST, UTC+2
        e = _vevent(eventout.build_ics(_ev("meeting 6 november 9:30")))
        assert _utc(e["DTSTART"].dt) == "2026-11-06 08:30"                    # CET, UTC+1


@test
def ics_timezone_setting_is_respected_and_bad_one_falls_back_to_utc():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "America/New_York"}}))
        assert _utc(_vevent(eventout.build_ics(_ev("meeting friday 9:30")))["DTSTART"].dt) == "2026-10-09 13:30"   # EDT
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Not/AZone"}}))
        assert eventout.local_zone() is None
        text = eventout.build_ics(_ev("meeting friday 9:30"))
        assert "DTSTART:" in text and "Z\r\n" in text and "TZID" not in text      # UTC instants, still correct


@test
def ics_all_day_multi_day_and_repeat():
    with isolated():
        e = _vevent(eventout.build_ics(_ev("holiday today until 31st october")))
        assert str(e["DTSTART"].dt) == "2026-10-07" and str(e["DTEND"].dt) == "2026-11-01"      # DTEND exclusive
        e = _vevent(eventout.build_ics(_ev("yoga every monday 7am")))
        assert e["RRULE"]["FREQ"] == ["WEEKLY"] and e["RRULE"]["BYDAY"] == ["MO"]


@test
def ics_title_escaping():
    with isolated():
        e = _vevent(eventout.build_ics(_ev("dentist, with Dr; Weber friday 3pm")))
        assert str(e["SUMMARY"]) == "Dentist, with Dr; Weber"


@test
def write_ics_goes_to_out_dir():
    with isolated() as tmp:
        path = eventout.write_ics(_ev("dentist friday 3pm"))
        assert path.parent == tmp / "out" and path.read_text().startswith("BEGIN:VCALENDAR")


# ------------------------------------------------------------------------------------------- google

import threading
import urllib.parse

import google_cal


class FakeResponse:
    def __init__(self, status=200, data=None):
        self.status_code, self._data = status, data if data is not None else {}

    def json(self):
        return self._data


class FakeHttp:
    """Stands in for `requests` inside google_cal. `script` maps (METHOD, url-suffix) to a list of responses."""

    def __init__(self, script=None):
        self.script, self.calls = script or {}, []

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        for (m, suffix), replies in self.script.items():
            if m == method and url.endswith(suffix) and replies:
                return replies.pop(0) if len(replies) > 1 else replies[0]
        return FakeResponse(404, {"error": {"message": "not scripted: %s %s" % (method, url)}})

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def request(self, method, url, **kwargs):
        return self._answer(method, url, kwargs)


@contextlib.contextmanager
def google_env(script=None, config=None, unlocked=True):
    with isolated():
        settings.CONFIG.write_text(json.dumps(config if config is not None else
                                              {"google": {"client_id": "cid", "calendar_id": "primary", "calendar_name": "Me"}}))
        if unlocked:
            vault.create("pass-phrase-1", {google_cal.SECRET_CLIENT: "csecret", google_cal.SECRET_REFRESH: "r-tok"})
        fake = FakeHttp(script)
        saved = (google_cal._http, dict(google_cal._token))
        google_cal._http = fake
        google_cal._token.update(value=None, expires=0.0)
        try:
            yield fake
        finally:
            google_cal._http = saved[0]
            google_cal._token.update(saved[1])


def _query(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


def _browser(code="abc", state=None, error=None):
    """An open_url() that plays the browser: follows Google's redirect back to the loopback listener."""
    import requests as real_requests

    def open_url(url):
        q = _query(url)
        open_url.seen = q

        def hit():
            time.sleep(0.2)
            params = {"error": error} if error else {"code": code, "state": state or q["state"]}
            real_requests.get(q["redirect_uri"] + "/?" + urllib.parse.urlencode(params), timeout=5)
        threading.Thread(target=hit, daemon=True).start()
    return open_url


def _timed(text="meeting friday 9:30"):
    return _ev(text)


@test
def google_payload_timed_all_day_multi_day_and_repeat():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Europe/Berlin"}}))
        p = google_cal.event_payload(_timed())
        assert p == {"summary": "Meeting", "start": {"dateTime": "2026-10-09T09:30:00", "timeZone": "Europe/Berlin"},
                     "end": {"dateTime": "2026-10-09T10:30:00", "timeZone": "Europe/Berlin"}}, p
        p = google_cal.event_payload(_ev("holiday today until 31st october"))
        assert p["start"] == {"date": "2026-10-07"} and p["end"] == {"date": "2026-11-01"} and "recurrence" not in p      # end exclusive
        p = google_cal.event_payload(_ev("sprint review friday next week"))
        assert p["start"] == {"date": "2026-10-16"} and p["end"] == {"date": "2026-10-17"}
        p = google_cal.event_payload(_ev("yoga every monday 7am"))
        assert p["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"] and p["start"]["timeZone"] == "Europe/Berlin"
        p = google_cal.event_payload(_ev("retreat 12 october 10am to 14 october 4pm"))
        assert p["start"]["dateTime"] == "2026-10-12T10:00:00" and p["end"]["dateTime"] == "2026-10-14T16:00:00"


@test
def google_payload_utc_fallback_when_zone_unknown():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Not/AZone"}}))
        p = google_cal.event_payload(_timed())
        assert p["start"]["timeZone"] == "UTC" and p["start"]["dateTime"].endswith("Z") and p["end"]["timeZone"] == "UTC"


@test
def google_pkce_and_auth_url():
    verifier, challenge = google_cal.new_pkce()
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert 43 <= len(verifier) <= 128 and "=" not in verifier + challenge
    assert google_cal.new_pkce()[0] != verifier
    q = _query(google_cal.build_auth_url("cid", "http://127.0.0.1:5555", "st", challenge))
    assert q["scope"] == google_cal.SCOPE and q["code_challenge_method"] == "S256" and q["code_challenge"] == challenge
    assert q["state"] == "st" and q["redirect_uri"] == "http://127.0.0.1:5555" and q["access_type"] == "offline"
    assert q["response_type"] == "code" and q["client_id"] == "cid"


@test
def google_sign_in_success_uses_pkce_and_state():
    with google_env({("POST", "/token"): [FakeResponse(200, {"refresh_token": "R", "access_token": "A", "expires_in": 3599})]}) as fake:
        browser = _browser()
        tokens = google_cal.sign_in("cid", "csecret", open_url=browser, timeout=10)
        assert tokens["refresh_token"] == "R"
        sent = fake.calls[0][2]["data"]
        assert sent["grant_type"] == "authorization_code" and sent["code"] == "abc" and sent["client_secret"] == "csecret"
        want = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert want == browser.seen["code_challenge"]                         # verifier matches the challenge Google saw
        assert sent["redirect_uri"] == browser.seen["redirect_uri"]


@test
def google_sign_in_rejects_wrong_state_denial_timeout_and_cancel():
    with google_env({("POST", "/token"): [FakeResponse(200, {"refresh_token": "R"})]}) as fake:
        for kwargs, needle in [({"open_url": _browser(state="forged")}, "state"),
                               ({"open_url": _browser(error="access_denied")}, "access_denied")]:
            try:
                google_cal.sign_in("cid", "s", timeout=10, **kwargs)
                raise AssertionError("accepted: " + needle)
            except google_cal.GoogleError as err:
                assert needle in str(err), str(err)
        assert not fake.calls                                                    # never reached the token endpoint
        try:
            google_cal.sign_in("cid", "s", open_url=lambda url: None, timeout=1)
            raise AssertionError("no timeout")
        except google_cal.GoogleError as err:
            assert "timed out" in str(err)
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        try:
            google_cal.sign_in("cid", "s", open_url=lambda url: None, timeout=10, cancel=cancel)
            raise AssertionError("not cancelled")
        except google_cal.GoogleError as err:
            assert "cancel" in str(err)


@test
def google_sign_in_needs_a_refresh_token():
    with google_env({("POST", "/token"): [FakeResponse(200, {"access_token": "A"})]}):
        try:
            google_cal.sign_in("cid", "s", open_url=_browser(), timeout=10)
            raise AssertionError("accepted a sign-in without a refresh token")
        except google_cal.GoogleError as err:
            assert "long-lived" in str(err)


@test
def google_sign_in_ignores_stray_requests():
    with google_env({("POST", "/token"): [FakeResponse(200, {"refresh_token": "R", "access_token": "A"})]}):
        import requests as real_requests

        def open_url(url):
            q = _query(url)

            def hit():
                time.sleep(0.2)
                real_requests.get(q["redirect_uri"] + "/favicon.ico", timeout=5)               # no code: must be ignored
                real_requests.get(q["redirect_uri"] + "/?code=abc&state=" + q["state"], timeout=5)
            threading.Thread(target=hit, daemon=True).start()
        assert google_cal.sign_in("cid", "s", open_url=open_url, timeout=10)["refresh_token"] == "R"


@test
def google_access_token_cache_refresh_and_errors():
    token = FakeResponse(200, {"access_token": "A1", "expires_in": 3600})
    with google_env({("POST", "/token"): [token]}) as fake:
        assert google_cal._access_token() == "A1" and google_cal._access_token() == "A1"
        assert len(fake.calls) == 1 and fake.calls[0][2]["data"]["refresh_token"] == "r-tok"             # cached second time
        google_cal._token["expires"] = 0.0
        google_cal._access_token()
        assert len(fake.calls) == 2                                                                      # expired -> renewed
    with google_env({("POST", "/token"): [FakeResponse(400, {"error": "invalid_grant", "error_description": "Token expired"})]}):
        try:
            google_cal._access_token()
            raise AssertionError("no error")
        except google_cal.GoogleError as err:
            assert "expired" in str(err) and "Production" in str(err)
    with google_env(unlocked=False):
        vault.create("pass-phrase-1", {})
        vault.lock()
        raises(vault.Locked, google_cal._access_token)


@test
def google_add_event_success_retry_and_errors():
    ok = FakeResponse(200, {"id": "evt"})
    token = FakeResponse(200, {"access_token": "A", "expires_in": 3600})
    with google_env({("POST", "/token"): [token], ("POST", "/events"): [ok]}) as fake:
        assert google_cal.add_event(_timed()) == "Me"
        url = [c for c in fake.calls if c[1].endswith("/events")][0][1]
        assert url == google_cal.API + "/calendars/primary/events"
    cfg = {"google": {"client_id": "cid", "calendar_id": "abc@group.calendar.google.com", "calendar_name": "Team"}}
    with google_env({("POST", "/token"): [token], ("POST", "/events"): [FakeResponse(401, {}), ok]}, config=cfg) as fake:
        assert google_cal.add_event(_timed()) == "Team"                                                  # 401 -> new token -> retry
        assert sum(1 for c in fake.calls if c[1].endswith("/token")) == 2
        assert "abc%40group.calendar.google.com" in fake.calls[1][1]                                     # calendar id is URL-quoted
    denied = FakeResponse(403, {"error": {"message": "Calendar API has not been used in project 1 before."}})
    with google_env({("POST", "/token"): [token], ("POST", "/events"): [denied]}):
        try:
            google_cal.add_event(_timed())
            raise AssertionError("no error")
        except google_cal.GoogleError as err:
            assert "has not been used" in str(err)
    with google_env(config={"google": {}}):
        raises(google_cal.GoogleError, google_cal.add_event, _timed())


@test
def google_list_calendars_parses_names_and_primary():
    items = {"items": [{"id": "me@gmail.com", "summary": "me@gmail.com", "primary": True},
                       {"id": "x", "summary": "Work", "summaryOverride": "My work"}, {"id": "y"}]}
    script = {("POST", "/token"): [FakeResponse(200, {"access_token": "A"})], ("GET", "/calendarList"): [FakeResponse(200, items)]}
    with google_env(script) as fake:
        got = google_cal.list_calendars()
        assert got == [{"id": "me@gmail.com", "name": "me@gmail.com", "primary": True},
                       {"id": "x", "name": "My work", "primary": False}, {"id": "y", "name": "y", "primary": False}], got
        assert fake.calls[1][2]["params"] == {"minAccessRole": "writer"}


@test
def google_save_connection_and_disconnect():
    with google_env({("POST", "/revoke"): [FakeResponse(200, {})]}, config={}, unlocked=False) as fake:
        vault.create("pass-phrase-1", {"apple_password": "keep-me"})
        google_cal.save_connection("cid", "csecret", "r-tok", "A", 3600)
        assert settings.load()["google"]["client_id"] == "cid"
        assert vault.get(google_cal.SECRET_REFRESH) == "r-tok" and google_cal._token["value"] == "A"
        settings.update({"google": {"calendar_id": "primary"}, "default_target": "google"})
        google_cal.disconnect()
        assert fake.calls[0][2]["data"] == {"token": "r-tok"}                                            # revoked at Google
        assert vault.names() == ["apple_password"]                                                       # other secrets untouched
        cfg = settings.load()
        assert cfg["google"] == {} and cfg["default_target"] is None and google_cal._token["value"] is None


@test
def google_disconnect_while_locked_changes_nothing():
    with google_env(config={"google": {"client_id": "cid", "calendar_id": "p"}}):
        vault.lock()
        raises(vault.Locked, google_cal.disconnect)
        assert settings.load()["google"]["client_id"] == "cid"


# ----------------------------------------------------------------------------------------- commands

import commands


def _both_configured(default=None):
    cfg = {"apple_id": "a", "calendar": "c", "google": {"client_id": "x", "calendar_id": "p"}}
    if default:
        cfg["default_target"] = default
    settings.CONFIG.write_text(json.dumps(cfg))


@test
def commands_split_override():
    cases = {"dentist friday 3pm /google": ("dentist friday 3pm", "google"),
             "dentist friday 3pm /Apple  ": ("dentist friday 3pm", "apple"),
             "dentist friday 3pm": ("dentist friday 3pm", None),
             "dentist/google": ("dentist/google", None),                   # no space before the slash: part of the title
             "meeting /apple friday 3pm": ("meeting /apple friday 3pm", None),   # only a trailing one counts
             "/google": ("/google", None),                                 # alone it is a command
             "dentist /outlook": ("dentist /outlook", None)}
    for text, want in cases.items():
        assert commands.split_override(text) == want, (text, commands.split_override(text))


@test
def commands_matching_and_tab_completion():
    assert commands.is_command("/x") and commands.is_command("  /x") and not commands.is_command("dentist /x")
    assert commands.matches("/s") == ["settings", "status"] and commands.matches("/zzz") == []
    assert commands.matches("/") == list(commands.COMMANDS)
    cases = {"/se": "/settings ", "/st": "/status ", "/s": "/s", "/lo": "/lo", "/loc": "/lock ", "/ta": "/target ",
             "/target ": "/target ", "/target ap": "/target ap", "/zzz": "/zzz", "/": "/"}
    for text, want in cases.items():
        assert commands.complete(text) == want, (text, commands.complete(text), want)
    title, detail = commands.suggestions("/st")
    assert title.startswith("/status") and "/status" in detail
    assert commands.suggestions("/zzz")[0] == "No command /zzz"


@test
def commands_run_help_unknown_and_prefix():
    with isolated():
        r = commands.run("/help")
        assert r.kind == "info" and all("/" + n in r.detail for n in commands.COMMANDS)
        assert commands.run("/zzz").kind == "error"
        assert commands.run("/s").kind == "error"                          # ambiguous: settings or status
        assert commands.run("/sett").action == "settings"                  # unique prefix runs
        assert commands.run("/").title == "Commands"                       # a bare slash shows the list


@test
def commands_actions():
    with isolated():
        for name, action in [("settings", "settings"), ("restart", "restart"), ("quit", "quit"), ("log", "log")]:
            r = commands.run("/" + name)
            assert r.action == action and r.autohide, (name, r)


@test
def commands_target_and_default_switching():
    with isolated():
        _both_configured()
        assert settings.default_target() == "apple"
        r = commands.run("/google")
        assert r.kind == "ok" and settings.load()["default_target"] == "google" and settings.default_target() == "google"
        assert commands.run("/target apple").kind == "ok" and settings.default_target() == "apple"
        info = commands.run("/target")
        assert info.kind == "info" and "Apple Calendar" in info.title and "Google Calendar" in info.detail
        assert commands.run("/target outlook").kind == "error"
    with isolated():
        settings.CONFIG.write_text(json.dumps({"apple_id": "a", "calendar": "c"}))
        r = commands.run("/google")                                        # not set up: refuse, change nothing
        assert r.kind == "error" and "not set up" in r.title and settings.load()["default_target"] is None


@test
def commands_lock_and_status():
    with isolated():
        _both_configured("google")
        assert "no vault" in commands.run("/status").detail
        vault.create("pass-phrase-1", {"k": "v"})
        detail = commands.run("/status").detail
        assert "Default: Google Calendar" in detail and "unlocked, locks in" in detail and "Apple Calendar, Google Calendar" in detail
        r = commands.run("/lock")
        assert r.kind == "ok" and vault.needs_unlock() and "Vault: locked" in commands.run("/status").detail


# ------------------------------------------------------------------------------------------ runner

def main():
    verbose = "-v" in sys.argv
    failed = 0
    for fn in TESTS:
        try:
            fn()
            if verbose:
                print("ok    %s" % fn.__name__)
        except Exception:  # noqa: BLE001
            failed += 1
            print("FAIL  %s\n%s" % (fn.__name__, "".join("      " + l for l in traceback.format_exc().splitlines(True)[-4:])))
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Backend tests: vault, settings, calendar choice, calendar-file output (and, later, Google and commands).

    python test_backend.py [-v]

Everything runs against temporary files; the real ~/.bamboo.* and ~/.quickadd.* files are never touched.
"""
import base64
import queue as queue_mod
import struct
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
import commands
import eventout
import google_cal
import settings
import vault
import voice

# Safety net: from import time on, nothing in this process can reach the real ~/.bamboo.* or ~/.quickadd.* files.
_SAFE = Path(tempfile.mkdtemp(prefix="bamboo-test-"))
vault.VAULT, settings.CONFIG, eventout.OUT_DIR = _SAFE / "v.vault", _SAFE / "c.json", _SAFE / "out"
google_cal.CLIENT_FILE = _SAFE / "google_client.json"
voice.FORCE = "plain"        # tests compare exact wording; the voice has its own tests below

ROOT_DIR = Path(__file__).resolve().parent
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


@contextlib.contextmanager
def isolated():
    """Point vault/settings/output at a temp folder and reset the in-memory unlock state."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        saved = (vault.VAULT, settings.CONFIG, eventout.OUT_DIR, vault.UNLOCK_IDLE_SECONDS, google_cal.CLIENT_FILE, voice.FORCE)
        vault.VAULT, settings.CONFIG, eventout.OUT_DIR = tmp / "v.vault", tmp / "c.json", tmp / "out"
        google_cal.CLIENT_FILE = tmp / "google_client.json"            # absent unless a test writes one
        vault.lock()
        try:
            yield tmp
        finally:
            vault.lock()
            vault.VAULT, settings.CONFIG, eventout.OUT_DIR, vault.UNLOCK_IDLE_SECONDS, google_cal.CLIENT_FILE, voice.FORCE = saved


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


import google_link


def _link_query(url):
    parts = urllib.parse.urlparse(url)
    assert parts.scheme == "https" and parts.netloc == "calendar.google.com" and parts.path == "/calendar/render", url
    return {k: v[0] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}


@test
def link_url_timed_event_uses_utc_instants_and_the_display_zone():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Europe/Berlin"}}))
        q = _link_query(google_link.build_url(_timed()))                              # Friday 9:30, summer time
        assert q == {"action": "TEMPLATE", "text": "Meeting", "dates": "20261009T073000Z/20261009T083000Z", "ctz": "Europe/Berlin"}, q
        q = _link_query(google_link.build_url(_ev("meeting 6 november 9:30")))        # winter time
        assert q["dates"] == "20261106T083000Z/20261106T093000Z"
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "America/New_York"}}))
        q = _link_query(google_link.build_url(_timed()))
        assert q["dates"] == "20261009T133000Z/20261009T143000Z" and q["ctz"] == "America/New_York"


@test
def link_url_all_day_multi_day_and_repeat():
    with isolated():
        q = _link_query(google_link.build_url(_ev("sprint review friday next week")))
        assert q["dates"] == "20261016/20261017" and "ctz" not in q                   # exclusive end, no timezone for dates
        q = _link_query(google_link.build_url(_ev("holiday today until 31st october")))
        assert q["dates"] == "20261007/20261101"
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Europe/Berlin"}}))
        q = _link_query(google_link.build_url(_ev("yoga every monday 7am")))
        assert q["recur"] == "RRULE:FREQ=WEEKLY;BYDAY=MO" and q["dates"] == "20261012T050000Z/20261012T060000Z", q
        q = _link_query(google_link.build_url(_ev("retreat 12 october 10am to 14 october 4pm")))
        assert q["dates"] == "20261012T080000Z/20261014T140000Z"


@test
def link_url_survives_special_characters_and_long_titles():
    with isolated():
        for title in ["Dentist & co #1 100% +x", "Café Müller – Übung", "a=b?c/d"]:
            ev = _ev("%s friday 3pm" % title)
            assert _link_query(google_link.build_url(ev))["text"] == ev["title"], title
        ev = _ev("%s friday 3pm" % ("x" * 500))
        assert len(_link_query(google_link.build_url(ev))["text"]) == google_link.MAX_TITLE


@test
def link_url_without_a_known_zone_omits_ctz_but_keeps_instants():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"defaults": {"timezone": "Not/AZone"}}))
        q = _link_query(google_link.build_url(_timed()))
        assert "ctz" not in q and q["dates"].count("Z") == 2 and q["dates"].count("/") == 1


@test
def link_mode_settings_logic():
    with isolated():
        cases = [({}, None, False), ({"google": {"mode": "link"}}, "link", True),
                 ({"google": {"client_id": "x", "calendar_id": "p"}}, "api", True),            # written before modes existed
                 ({"google": {"client_id": "x"}}, "api", False), ({"google": {"mode": "api"}}, "api", False),
                 ({"google": {"mode": "link", "client_id": "x"}}, "link", True), ({"google": {"mode": "bogus"}}, None, False)]
        for raw, mode, configured in cases:
            settings.CONFIG.write_text(json.dumps(raw))
            assert settings.google_mode() == mode and settings.google_configured() == configured, (raw, settings.google_mode())
        settings.CONFIG.write_text(json.dumps({"google": {"mode": "link"}, "default_target": "google"}))
        assert settings.configured_targets() == ["google"] and settings.default_target() == "google"


@test
def link_mode_routing_needs_no_unlock_and_opens_the_browser():
    with isolated():
        settings.CONFIG.write_text(json.dumps({"apple_id": "a", "calendar": "c", "google": {"mode": "link"}}))
        vault.create("pass-phrase-1", {"apple_password": "x"})
        vault.lock()
        assert calendars.is_manual("google") and not calendars.is_manual("apple")
        assert calendars.needs_unlock("google") is False and calendars.needs_unlock("apple") is True      # Apple still needs the vault
        assert calendars.footer_label("google") == "Google Calendar (opens in your browser)" and calendars.footer_label("apple") == "Apple Calendar"
        opened = []
        saved = google_link._open
        google_link._open = lambda url: opened.append(url) or True
        try:
            assert calendars.add_event("google", _timed(), "ics") == "your browser" and len(opened) == 1
            assert _link_query(opened[0])["text"] == "Meeting"
            google_link._open = lambda url: False                                                          # no browser could start
            raises(RuntimeError, calendars.add_event, "google", _timed(), "ics")
        finally:
            google_link._open = saved
        assert vault.needs_unlock()                                                                         # the vault was never touched
    with isolated():
        settings.CONFIG.write_text(json.dumps({"google": {"mode": "api", "client_id": "x", "calendar_id": "p"}}))
        vault.create("pass-phrase-1", {})
        vault.lock()
        assert not calendars.is_manual("google") and calendars.needs_unlock("google") is True               # the API route still unlocks


BUNDLED = ("123456789012-abcdefg.apps.googleusercontent.com", "GOCSPX-shipped-secret")


def _ship_client(client_id=BUNDLED[0], secret=BUNDLED[1]):
    google_cal.CLIENT_FILE.write_text(json.dumps({"client_id": client_id, "client_secret": secret}))


@test
def google_bundled_client_file_parsing():
    with isolated():
        assert google_cal.bundled_client() is None                                   # no file shipped
        _ship_client()
        assert google_cal.bundled_client() == BUNDLED
        for bad in [{"client_id": "PASTE-THE-CLIENT-ID.apps.googleusercontent.com", "client_secret": "PASTE-THE-CLIENT-SECRET"},
                    {"client_id": "not-a-client-id", "client_secret": "x"},
                    {"client_id": BUNDLED[0], "client_secret": ""}, {"client_id": BUNDLED[0]}, {}]:
            google_cal.CLIENT_FILE.write_text(json.dumps(bad))
            assert google_cal.bundled_client() is None, bad
        google_cal.CLIENT_FILE.write_text("{ not json")
        assert google_cal.bundled_client() is None
        example = Path(__file__).with_name("google_client.example.json")
        google_cal.CLIENT_FILE = example
        assert google_cal.bundled_client() is None                                   # the shipped example is a placeholder


@test
def google_client_credentials_precedence():
    with google_env():                                                               # vault holds an own-project secret
        assert google_cal.using_own_client() and google_cal.client_credentials() == ("cid", "csecret")     # nothing shipped
        _ship_client()
        assert not google_cal.using_own_client() and google_cal.client_credentials() == BUNDLED            # shipped client wins
        vault.lock()
        assert google_cal.client_credentials() == BUNDLED                            # needs no vault at all
        vault.unlock("pass-phrase-1")
        settings.update({"google": {"own_client": True}})
        assert google_cal.using_own_client() and google_cal.client_credentials() == ("cid", "csecret")     # user opted out


@test
def google_token_renewal_uses_the_shipped_client():
    token = FakeResponse(200, {"access_token": "A", "expires_in": 3600})
    with google_env({("POST", "/token"): [token]}) as fake:
        vault.delete(google_cal.SECRET_CLIENT)                                       # as after a shipped-client sign-in
        _ship_client()
        settings.update({"google": {"client_id": BUNDLED[0]}})
        assert google_cal._access_token() == "A"
        sent = fake.calls[0][2]["data"]
        assert sent["client_id"] == BUNDLED[0] and sent["client_secret"] == BUNDLED[1] and sent["refresh_token"] == "r-tok"


@test
def google_save_connection_shipped_vs_own_client():
    with google_env(config={}, unlocked=False):
        vault.create("pass-phrase-1", {})
        google_cal.save_connection(BUNDLED[0], BUNDLED[1], "r1")
        assert vault.names() == [google_cal.SECRET_REFRESH]                          # the shared secret is not copied per user
        assert settings.load()["google"] == {"mode": "api", "client_id": BUNDLED[0], "own_client": False}
        google_cal.save_connection("own-id", "own-secret", "r2", own_client=True)
        assert vault.get(google_cal.SECRET_CLIENT) == "own-secret" and vault.get(google_cal.SECRET_REFRESH) == "r2"
        assert settings.load()["google"] == {"mode": "api", "client_id": "own-id", "own_client": True}
        google_cal.save_connection(BUNDLED[0], BUNDLED[1], "r3")                     # switching back drops the own secret
        assert google_cal.SECRET_CLIENT not in vault.names()


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


# ------------------------------------------------------------------------------------------ migration

import paths


@contextlib.contextmanager
def fake_home():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@test
def migration_copies_old_files_byte_for_byte_and_keeps_the_originals():
    with fake_home() as h:
        data = {".quickadd.json": b'{"apple_id": "a"}\r\n', ".quickadd.vault": os.urandom(300), ".quickadd.vault.bak": b"older"}
        for name, blob in data.items():
            (h / name).write_bytes(blob)
        assert sorted(paths.migrate(h)) == [".bamboo.json", ".bamboo.vault", ".bamboo.vault.bak"]
        for old, new in paths.migration_pairs(h):
            assert new.read_bytes() == old.read_bytes() == data[old.name] and old.exists(), old.name
        assert paths.migrate(h) == []                                              # a second run does nothing
        assert not list(h.glob("*.tmp"))                                           # no temp files left behind


@test
def migration_never_overwrites_a_new_file_and_ignores_missing_ones():
    with fake_home() as h:
        (h / ".quickadd.json").write_bytes(b"old settings")
        (h / ".bamboo.json").write_bytes(b"new settings")                           # the new file already exists: it wins
        (h / ".quickadd.vault").write_bytes(b"old vault")                           # only this one is copied
        assert paths.migrate(h) == [".bamboo.vault"]
        assert (h / ".bamboo.json").read_bytes() == b"new settings" and (h / ".quickadd.json").read_bytes() == b"old settings"
    with fake_home() as h:
        assert paths.migrate(h) == [] and not list(h.iterdir())                    # nothing to migrate, nothing created
    with fake_home() as h:
        (h / ".quickadd.json").write_bytes(b"{ not json")                           # damaged settings are copied as they are
        assert paths.migrate(h) == [".bamboo.json"] and (h / ".bamboo.json").read_bytes() == b"{ not json"


@test
def migrated_vault_and_settings_work_under_the_new_names():
    with fake_home() as h, isolated():
        old_vault, old_cfg = h / ".quickadd.vault", h / ".quickadd.json"
        vault.create("pass-phrase-1", {"apple_password": "abcd-efgh"}, path=old_vault)
        vault.lock()
        old_cfg.write_text(json.dumps({"apple_id": "me@icloud.com", "calendar": "School"}))
        paths.migrate(h)
        new_vault, new_cfg = h / ".bamboo.vault", h / ".bamboo.json"
        assert new_vault.read_bytes() == old_vault.read_bytes()                      # not re-encrypted
        assert vault.unlock("pass-phrase-1", path=new_vault) and vault.get("apple_password") == "abcd-efgh"
        cfg = settings.load(new_cfg)
        assert settings.apple_configured(cfg) and cfg["apple_id"] == "me@icloud.com"


@test
def path_constants_use_the_new_names():
    assert paths.CONFIG.name == ".bamboo.json" and paths.VAULT.name == ".bamboo.vault" and paths.OUT_DIR.name == "Bamboo"
    assert paths.KEYRING_SERVICE == "quickadd-icloud"                               # kept so stored credentials are not orphaned



# ------------------------------------------------------------------------------- rename: old instance, shortcuts

import uuid

import bamboo_ctl
import singleton


@contextlib.contextmanager
def fake_singleton_names():
    """Unique mutex/event names, so these tests can never see (or stop) the real running popup."""
    tag = uuid.uuid4().hex[:8]
    saved = (singleton.MUTEX_NAME, singleton.STOP_NAME, singleton.LEGACY_MUTEX_NAME, singleton.LEGACY_STOP_NAME, list(singleton._held))
    singleton.MUTEX_NAME, singleton.STOP_NAME = "Local\\bamboo-test-%s-mutex" % tag, "Local\\bamboo-test-%s-stop" % tag
    singleton.LEGACY_MUTEX_NAME, singleton.LEGACY_STOP_NAME = "Local\\old-test-%s-mutex" % tag, "Local\\old-test-%s-stop" % tag
    singleton._held.clear()
    try:
        yield
    finally:
        for handle in singleton._held:
            singleton._k32.CloseHandle(handle)
        singleton._held[:] = saved[4]
        singleton.MUTEX_NAME, singleton.STOP_NAME, singleton.LEGACY_MUTEX_NAME, singleton.LEGACY_STOP_NAME = saved[:4]


@test
def singleton_allows_one_popup_and_signals_it_to_stop():
    with fake_singleton_names():
        assert not singleton.is_running() and singleton.request_stop() is False
        assert singleton.acquire() is True
        assert singleton.is_running() and singleton.acquire() is False             # a second copy is refused
        event = singleton.create_stop_event()
        assert not singleton.stop_requested(event)
        assert singleton.request_stop() is True and singleton.stop_requested(event)


@test
def singleton_sees_and_stops_a_popup_started_under_the_old_name():
    with fake_singleton_names():
        old_mutex = singleton._k32.CreateMutexW(None, False, singleton.LEGACY_MUTEX_NAME)     # what the old version holds
        old_stop = singleton._k32.CreateEventW(None, True, False, singleton.LEGACY_STOP_NAME)
        try:
            assert singleton.is_running()                                                        # `bamboo status` sees it
            assert singleton.acquire() is False                                                  # a new popup does not start beside it
            assert singleton.stop_requested(old_stop) is False
            assert singleton.request_stop() is True and singleton.stop_requested(old_stop)       # `bamboo restart` can stop it
        finally:
            singleton._k32.CloseHandle(old_mutex)
            singleton._k32.CloseHandle(old_stop)
        assert not singleton.is_running()


@contextlib.contextmanager
def fake_startup():
    with tempfile.TemporaryDirectory() as tmp:
        saved = (bamboo_ctl.STARTUP_DIR, bamboo_ctl._write_shortcut)
        bamboo_ctl.STARTUP_DIR = Path(tmp)
        bamboo_ctl._write_shortcut = lambda path: Path(path).write_text("shortcut")
        try:
            yield Path(tmp)
        finally:
            bamboo_ctl.STARTUP_DIR, bamboo_ctl._write_shortcut = saved


@test
def autostart_shortcut_uses_the_new_name_and_replaces_the_old_one():
    with fake_startup() as d:
        assert bamboo_ctl.autostart_enabled() is False and bamboo_ctl.upgrade_autostart() is False and not list(d.iterdir())
        bamboo_ctl.set_autostart(True)
        assert [f.name for f in d.iterdir()] == ["Bamboo.lnk"] and bamboo_ctl.autostart_enabled()
        bamboo_ctl.set_autostart(False)
        assert not list(d.iterdir()) and not bamboo_ctl.autostart_enabled()
    with fake_startup() as d:
        (d / "Quickadd.lnk").write_text("old shortcut")                                          # made before the rename
        assert bamboo_ctl.autostart_enabled() is True                                            # still counted as on
        assert bamboo_ctl.upgrade_autostart() is True
        assert [f.name for f in d.iterdir()] == ["Bamboo.lnk"]                                   # replaced, old one gone
        assert bamboo_ctl.upgrade_autostart() is False                                           # nothing left to upgrade
    with fake_startup() as d:
        (d / "Quickadd.lnk").write_text("old shortcut")
        bamboo_ctl.set_autostart(False)
        assert not list(d.iterdir())                                                             # turning it off removes both


@test
def control_script_and_shims_point_at_the_new_program():
    assert bamboo_ctl.SCRIPT.name == "bamboo.py" and bamboo_ctl.SCRIPT.exists()
    assert (ROOT_DIR / "bamboo.bat").read_text().count("bamboo_ctl.py") == 1
    assert "bamboo" in (ROOT_DIR / "qa.bat").read_text() and "from bamboo import main" in (ROOT_DIR / "quickadd.py").read_text()
    assert "bamboo.bat" in (ROOT_DIR / "run_bamboo.bat").read_text()



# ------------------------------------------------------------------------------------ brand: voice, colours, artwork

import branding
import theme


@contextlib.contextmanager
def voice_mode(mode):
    saved = (voice.FORCE, voice._pick)
    voice.FORCE, voice._pick = mode, (lambda options: options[0])           # deterministic: always the first friendly line
    try:
        yield
    finally:
        voice.FORCE, voice._pick = saved


@test
def voice_every_situation_has_plain_and_friendly_wording():
    sample = {"calendar": "Apple Calendar", "name": "zzz"}
    for key, (plain, friendly) in voice.LINES.items():
        assert plain and 1 <= len(friendly) <= 3 and len(set(friendly)) == len(friendly), key
        for text in [plain] + friendly:
            text.format(**sample)                                              # no stray placeholder
            assert len(text.format(**sample)) <= 60, text                      # short enough for a card
            assert all(ord(c) < 0x1F000 for c in text), "emoji in " + key       # Tk renders emoji badly
        for placeholder in ("{calendar}", "{name}"):
            if placeholder in plain:
                assert all(placeholder in t for t in friendly), (key, placeholder)   # friendly never drops the useful part


@test
def voice_plain_is_the_original_wording_and_friendly_is_different():
    with isolated():
        with voice_mode("plain"):
            assert voice.say("saved", calendar="Apple Calendar") == "Added to Apple Calendar"
            assert voice.say("wrong_passphrase") == "Wrong passphrase" and voice.say("locked") == "Locked"
            assert voice.say("unknown_command", name="zzz") == "No command /zzz" and voice.say("empty") == "Describe your event"
        with voice_mode("friendly"):
            assert voice.say("saved", calendar="Apple Calendar") == "Done! Tucked into Apple Calendar"
            assert voice.say("wrong_passphrase") == "That passphrase didn't fit"
            assert "zzz" in voice.say("unknown_command", name="zzz")


@test
def voice_follows_the_setting_and_defaults_to_friendly():
    with isolated():
        with voice_mode(None):
            assert voice.enabled() is True                                      # nothing saved: friendly by default
            settings.CONFIG.write_text(json.dumps({"defaults": {"playful": False}}))
            assert voice.enabled() is False and voice.say("locked") == "Locked"
            settings.CONFIG.write_text(json.dumps({"defaults": {"playful": True}}))
            assert voice.enabled() is True
            settings.CONFIG.write_text(json.dumps({"defaults": {"playful": "yes please"}}))   # not a real boolean: default
            assert settings.load()["defaults"]["playful"] is True and voice.enabled() is True


@test
def voice_reaches_the_popup_and_the_commands():
    import bamboo
    with isolated(), voice_mode("friendly"):
        settings.CONFIG.write_text(json.dumps({"apple_id": "a", "calendar": "c"}))
        out = queue_mod.Queue()
        calendars_add = calendars.add_event
        calendars.add_event = lambda target, ev, ics: "School"
        try:
            bamboo.save_event(_ev("dentist friday 3pm"), "apple", out)
        finally:
            calendars.add_event = calendars_add
        kind, title, detail = out.get_nowait()
        assert kind == "saved" and title == "Done! Tucked into Apple Calendar" and "School" in detail and "Dentist" in detail
        assert commands.run("/zzzz").title.startswith("I don't know /zzzz") and commands.run("/lock").title == "Locked up safe"
    with isolated(), voice_mode("plain"):
        assert commands.run("/lock").title == "Locked" and commands.run("/zzzz").title == "No command /zzzz"


def _luminance(color):
    r, g, b = (int(color.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@test
def theme_contrast_meets_wcag():
    t = theme
    needs = [(t.FG, t.BG, 7), (t.FG, t.CARD, 7), (t.FG, t.FIELD, 7), (t.DIM, t.BG, 4.5), (t.DIM, t.CARD, 4.5), (t.DIM, t.FIELD, 4.5),
             (t.ACCENT, t.BG, 4.5), (t.ACCENT, t.CARD, 4.5), (t.ACCENT, t.FIELD, 4.5), (t.ON_FILL, t.ACCENT_FILL, 4.5),
             (t.OK, t.CARD, 4.5), (t.WARN, t.CARD, 4.5), (t.ERR, t.CARD, 4.5), (t.OK, t.BG, 4.5), (t.WARN, t.BG, 4.5), (t.ERR, t.BG, 4.5)]
    low = [(a, b, round(_contrast(a, b), 2), need) for a, b, need in needs if _contrast(a, b) < need]
    assert not low, low
    for token in ("BG", "CARD", "FIELD", "BORDER", "FG", "DIM", "ACCENT", "ACCENT_FILL", "OK", "WARN", "ERR", "ON_FILL"):
        assert len(getattr(t, token)) == 7 and getattr(t, token).startswith("#"), token


@test
def artwork_is_complete_and_the_icon_is_a_valid_ico():
    from PIL import Image
    for name in ("bamboo-logo.svg", "bamboo-tile.svg", "bamboo-face.svg", "bamboo-icon.svg", "banner.png", "bamboo.ico"):
        assert (ROOT_DIR / "assets" / name).stat().st_size > 500, name
    expected = {"tile": [48, 64, 96, 128, 256, 512], "face": [16, 20, 24, 32, 40, 48, 64], "icon": [16, 24, 32, 48, 64, 128, 256]}
    for kind, sizes in expected.items():
        assert branding.sizes(kind) == sizes, kind
        for size in sizes:
            image = Image.open(branding.PNG / ("%s-%d.png" % (kind, size)))
            assert image.size == (size, size) and image.mode == "RGBA", (kind, size)
    ico = (ROOT_DIR / "assets" / "bamboo.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind, count) == (0, 1, 7)
    for i in range(count):
        width, height, _c, _r, planes, bits, size, offset = struct.unpack("<BBBBHHII", ico[6 + 16 * i:22 + 16 * i])
        assert ico[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and planes == 1 and bits == 32 and size > 100
    assert Image.open(ROOT_DIR / "assets" / "banner.png").size == (1200, 360)


@test
def branding_picks_sizes_and_survives_missing_files():
    assert branding.nearest("icon", 64) == 64 and branding.nearest("icon", 20) == 24 and branding.nearest("icon", 5000) == 256
    assert branding.nearest("tile", 88) == 96 and branding.nearest("nothing", 32) is None
    assert branding.pil("icon", 64).size == (64, 64) and branding.pil("icon", 40).size == (40, 40)
    assert branding.pil("nothing", 32) is None and branding.path("nothing", 32) is None
    saved = branding.PNG
    branding.PNG = ROOT_DIR / "no-such-folder"
    try:
        assert branding.nearest("icon", 32) is None and branding.pil("icon", 32) is None          # a damaged install still starts
    finally:
        branding.PNG = saved


@test
def tray_uses_the_panda_icon():
    import tray
    image = tray._icon_image()
    assert image.size == (64, 64) and image.mode == "RGBA"
    corner, centre = image.getpixel((2, 2)), image.getpixel((32, 32))
    assert corner[3] < 40 and centre[3] == 255                                  # rounded tile: transparent corner, solid middle


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

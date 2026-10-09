"""Google Calendar over its REST API, using OAuth for installed apps (loopback redirect + PKCE).

You create the OAuth client yourself (Google Cloud Console -> Desktop app); see GUIDE below. Nothing here
ever leaves your PC except calls to Google's own endpoints.

Where things live:
  settings (plain text, ~/.quickadd.json):  google.client_id, google.calendar_id, google.calendar_name
  vault (encrypted, ~/.quickadd.vault):     google_client_secret, google_refresh_token
  memory only, a few minutes:               the short-lived access token

Scope is calendar.events (create/edit events). Tokens and secrets are never printed or logged.
"""
import base64
import datetime as dt
import hashlib
import http.server
import json
import re
import secrets as _secrets
import threading
import time
import urllib.parse
import webbrowser

import requests

import eventout
import settings
import vault

from pathlib import Path  # noqa: E402

CLIENT_FILE = Path(__file__).with_name("google_client.json")      # the shared client shipped with the app
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.events"
SECRET_CLIENT = "google_client_secret"
SECRET_REFRESH = "google_refresh_token"
TIMEOUT = 20

DEVELOPER_SETUP = """Once, by whoever ships the app (about 10 minutes, free). Everyone else just clicks "Sign in with Google".
 1. console.cloud.google.com -> create a project named like the app (users see this name when they sign in).
 2. APIs & Services -> Library -> enable "Google Calendar API".
 3. APIs & Services -> OAuth consent screen -> External. Set the app name, a logo, your support e-mail, and add
    the scope .../auth/calendar.events.
 4. Credentials -> Create credentials -> OAuth client ID -> "Desktop app". Copy the client ID and secret.
 5. Copy google_client.example.json to google_client.json next to the program and paste both values in.
 6. On the consent screen choose "Publish app" (In production). Until you do, Google expires every sign-in
    after 7 days. Unverified apps show a "Google hasn't verified this app" page that users click through
    (Advanced -> Go to ... (unsafe)); full verification (website, privacy policy, video) removes it."""

GUIDE = """One-time setup in Google Cloud (about 10 minutes, free):
 1. console.cloud.google.com -> create a project (any name).
 2. APIs & Services -> Library -> enable "Google Calendar API".
 3. APIs & Services -> OAuth consent screen -> External. Fill the app name and your e-mail. Add the
    scope .../auth/calendar.events. Add your own Google account as a test user.
 4. Credentials -> Create credentials -> OAuth client ID -> application type "Desktop app".
 5. Copy the client ID and client secret into quick-add (Settings -> Accounts -> Google), then Sign in.
 6. IMPORTANT: back on the consent screen, choose "Publish app" (In production). While the app stays in
    "Testing", Google expires the sign-in after 7 days. For your own use, the "unverified app" warning
    at sign-in can be accepted."""

_http = requests                                   # tests swap this for a fake
_token = {"value": None, "expires": 0.0}


class GoogleError(RuntimeError):
    """A problem with Google sign-in or the Calendar API, worded for the user."""


# ------------------------------------------------------------------------------ OAuth helpers

def new_pkce():
    """(verifier, challenge): a one-time secret and its SHA-256 fingerprint, sent in that order."""
    verifier = base64.urlsafe_b64encode(_secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_auth_url(client_id, redirect_uri, state, challenge):
    query = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
             "access_type": "offline", "prompt": "consent"}          # offline + consent => a refresh token is issued
    return AUTH_URL + "?" + urllib.parse.urlencode(query)


def _describe(response):
    try:
        body = response.json()
    except ValueError:
        return "HTTP %s" % response.status_code
    err = body.get("error")
    if isinstance(err, dict):                                        # Calendar API style
        return err.get("message") or "HTTP %s" % response.status_code
    if err:                                                          # OAuth style
        return "%s%s" % (err, (": " + body["error_description"]) if body.get("error_description") else "")
    return "HTTP %s" % response.status_code


class _Redirect(http.server.BaseHTTPRequestHandler):
    PAGE = (b"<!doctype html><meta charset=utf-8><title>Quick add</title>"
            b"<body style='font:16px Segoe UI,sans-serif;background:#17181d;color:#eceef4;padding:3em'>"
            b"<h2>Signed in.</h2><p>You can close this tab and go back to quick-add.</p>")

    def do_GET(self):
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()}
        if "code" in q or "error" in q:                              # ignore favicon and other stray requests
            self.server.result = q
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.PAGE)

    def log_message(self, *args):                                    # never log the request line (it holds the code)
        pass


def sign_in(client_id, client_secret, open_url=webbrowser.open, timeout=120, cancel=None):
    """Run the browser sign-in. Returns {"refresh_token", "access_token", "expires_in"}.
    `open_url(url)` opens the consent page (tests pass their own); `cancel` is a threading.Event."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirect)
    server.result = None
    server.timeout = 0.5
    redirect_uri = "http://127.0.0.1:%d" % server.server_address[1]
    state = _secrets.token_urlsafe(24)
    verifier, challenge = new_pkce()
    try:
        open_url(build_auth_url(client_id, redirect_uri, state, challenge))
        deadline = time.monotonic() + timeout
        while server.result is None:
            if cancel is not None and cancel.is_set():
                raise GoogleError("Sign-in cancelled.")
            if time.monotonic() > deadline:
                raise GoogleError("Sign-in timed out. Try again.")
            server.handle_request()
    finally:
        server.server_close()
    got = server.result
    if got.get("error"):
        raise GoogleError("Google sign-in was refused (%s)." % got["error"])
    if got.get("state") != state:
        raise GoogleError("Sign-in answer did not match the request (state mismatch); nothing was saved.")
    if not got.get("code"):
        raise GoogleError("Google did not send a sign-in code.")
    response = _http.post(TOKEN_URL, data={"code": got["code"], "client_id": client_id, "client_secret": client_secret,
                                           "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                                           "code_verifier": verifier}, timeout=TIMEOUT)
    if response.status_code != 200:
        raise GoogleError("Google refused the sign-in: %s" % _describe(response))
    tokens = response.json()
    if not tokens.get("refresh_token"):
        raise GoogleError("Google gave no long-lived sign-in. Remove this app at myaccount.google.com/permissions and try again.")
    return tokens


def bundled_client():
    """(client_id, client_secret) shipped with the app in google_client.json, or None.

    This is the "Sign in with Google" most people use: the developer creates ONE Google OAuth client and
    ships it, so users never touch Google Cloud. Google treats an installed app's client secret as
    non-confidential (the sign-in is protected by PKCE instead). A placeholder file does not count."""
    try:
        data = json.loads(CLIENT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    client_id, secret = str(data.get("client_id", "")).strip(), str(data.get("client_secret", "")).strip()
    real = re.match(r"^\d{6,}-[\w-]+\.apps\.googleusercontent\.com$", client_id) and secret and not secret.upper().startswith("PASTE")
    return (client_id, secret) if real else None


def using_own_client():
    """True when the user chose their own Google project (or this build ships no client of its own)."""
    return bool(settings.load()["google"].get("own_client")) or bundled_client() is None


def client_credentials():
    """(client_id, client_secret) for sign-in and token renewal. Own-project secrets come from the vault."""
    g = settings.load()["google"]
    if not using_own_client():
        return bundled_client()
    return g.get("client_id"), vault.get(SECRET_CLIENT)


def save_connection(client_id, client_secret, refresh_token, access_token=None, expires_in=0, own_client=False):
    """Remember a finished sign-in. The vault must be unlocked (or just created). The shared client's
    secret is not stored per user; an own-project secret is, encrypted."""
    vault.put(SECRET_REFRESH, refresh_token)
    if own_client:
        vault.put(SECRET_CLIENT, client_secret)
    else:
        vault.delete(SECRET_CLIENT)
    settings.update({"google": {"client_id": client_id, "own_client": bool(own_client)}})
    if access_token:
        _token.update(value=access_token, expires=time.monotonic() + max(int(expires_in) - 60, 0))


def _access_token():
    if _token["value"] and time.monotonic() < _token["expires"]:
        return _token["value"]
    refresh = vault.get(SECRET_REFRESH)                                        # raises vault.Locked until unlocked
    client_id, secret = client_credentials()
    if not (client_id and refresh and secret):
        raise GoogleError("Google is not signed in. Open Settings -> Accounts -> Google.")
    response = _http.post(TOKEN_URL, data={"client_id": client_id, "client_secret": secret, "refresh_token": refresh,
                                           "grant_type": "refresh_token"}, timeout=TIMEOUT)
    if response.status_code != 200:
        problem = _describe(response)
        if "invalid_grant" in problem:
            raise GoogleError("Google sign-in expired or was revoked. Sign in again in Settings (and publish the "
                              "app to Production to stop the 7-day expiry).")
        raise GoogleError("Google refused to renew the sign-in: %s" % problem)
    body = response.json()
    _token.update(value=body["access_token"], expires=time.monotonic() + max(int(body.get("expires_in", 3600)) - 60, 0))
    return _token["value"]


# ------------------------------------------------------------------------------ Calendar API

def _call(method, path, **kwargs):
    for attempt in (0, 1):
        headers = {"Authorization": "Bearer " + _access_token()}
        response = _http.request(method, API + path, headers=headers, timeout=TIMEOUT, **kwargs)
        if response.status_code == 401 and attempt == 0:
            _token.update(value=None, expires=0.0)                   # token went stale: renew once and retry
            continue
        return response


def list_calendars():
    """[{"id", "name", "primary"}] for calendars you can add events to."""
    response = _call("GET", "/users/me/calendarList", params={"minAccessRole": "writer"})
    if response.status_code != 200:
        raise GoogleError("Could not list calendars: %s" % _describe(response))
    items = response.json().get("items", [])
    return [{"id": c["id"], "name": c.get("summaryOverride") or c.get("summary") or c["id"], "primary": bool(c.get("primary"))}
            for c in items]


def event_payload(ev):
    """The Calendar API event resource for a parsed event (see eventparser.parse)."""
    body = {"summary": ev["title"]}
    if ev["all_day"]:
        body["start"] = {"date": ev["start"].isoformat()}
        body["end"] = {"date": (ev["end"] + dt.timedelta(days=1)).isoformat()}     # end date is exclusive
    else:
        found = eventout.local_zone()
        if found:
            name = found[0]
            body["start"] = {"dateTime": ev["start"].strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": name}
            body["end"] = {"dateTime": ev["end"].strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": name}
        else:
            as_utc = lambda v: v.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731 - naive = PC local time
            body["start"] = {"dateTime": as_utc(ev["start"]), "timeZone": "UTC"}
            body["end"] = {"dateTime": as_utc(ev["end"]), "timeZone": "UTC"}
    if ev.get("rrule"):
        body["recurrence"] = ["RRULE:" + ev["rrule"]]
    return body


def add_event(ev):
    """Create the event in the chosen calendar. Returns that calendar's name."""
    g = settings.load()["google"]
    if not (g.get("client_id") and g.get("calendar_id")):
        raise GoogleError("Google Calendar is not set up.")
    response = _call("POST", "/calendars/%s/events" % urllib.parse.quote(g["calendar_id"], safe=""),
                     json=event_payload(ev))
    if response.status_code not in (200, 201):
        raise GoogleError("Google Calendar: %s" % _describe(response))
    return g.get("calendar_name") or g["calendar_id"]


def disconnect():
    """Revoke the sign-in at Google (best effort), forget the secrets and the calendar choice.
    Raises vault.Locked, before changing anything, if the vault is locked."""
    refresh = vault.get(SECRET_REFRESH)
    if refresh:
        try:
            _http.post(REVOKE_URL, data={"token": refresh}, timeout=TIMEOUT)
        except Exception:  # noqa: BLE001 - revoking is a courtesy; the local secrets are removed regardless
            pass
    vault.delete(SECRET_REFRESH)
    vault.delete(SECRET_CLIENT)
    _token.update(value=None, expires=0.0)
    cfg = settings.load()
    cfg["google"] = {}
    if cfg.get("default_target") == "google":
        cfg["default_target"] = None
    settings.save(cfg)

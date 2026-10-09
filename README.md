<div align="center">

# 📅 Quick add

**Type it. Press Enter. It's in your calendar.**

A tiny Windows popup that turns plain sentences into calendar events and sends them to
Apple Calendar or Google Calendar. No forms, no clicking through a month view.

`dentist friday 3pm` &nbsp;→&nbsp; ✅ *Dentist, Fri 16 Oct, 15:00–16:00*

<sub>Windows 10/11 · Python 3.11 · runs in the tray · about 25&nbsp;MB of memory while idle</sub>

</div>

---

## ✨ What it does

- **Global hotkey** (default `Ctrl + Alt + C`) opens a small popup from anywhere.
- **Understands natural phrases**, shows you the result *before* anything is saved, then adds it.
- **Apple Calendar and Google Calendar**, one default plus a one-off override per event.
- **Private by design**: no server, no account with us. Passwords and tokens are encrypted on your PC.
- **Always visible in the tray**, with start/stop/restart and "start with Windows".

## 💬 Things you can type

| You type | You get |
|---|---|
| `dentist friday 3pm` | Fri 15:00 – 16:00 |
| `lunch with Sam tomorrow at 12:30` | Tomorrow 12:30 – 13:30 |
| `workshop from 9:30 180 minutes tomorrow` | Tomorrow 09:30 – 12:30 |
| `meeting friday 9 until 11` | Fri 09:00 – 11:00 |
| `holiday today until 31st october` | All-day, today → 31 Oct |
| `conference tomorrow morning 9 until 31 october 12pm` | Starts tomorrow 09:00, ends 31 Oct 12:00 |
| `workshop next week` · `trip next month` · `party this weekend` | All-day periods |
| `yoga every monday 7am` · `standup every weekday 9:30` | Repeating events |
| `night shift tomorrow 10pm to 6am` | Ends the next morning |
| `review tomorrow 5pm /google` | Sent to Google, just this once |

> **Good to know.** If you type no time, the event is all-day. If you type no length, it lasts the default
> (one hour). Small guesses such as "5 → 5 pm" are shown under the card so nothing happens silently.

## 🚀 Quick start

```powershell
git clone <this repo> quickadd
cd quickadd
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
qa start
```

Then press **Ctrl + Alt + C**, or use the calendar icon in the tray. Start it with Windows from the tray menu or with `qa autostart on`.

| Command | What it does |
|---|---|
| `qa start` / `qa stop` | Start or stop the popup |
| `qa restart` | Stop and start again, which loads code changes |
| `qa status` | Running? Starts with Windows? |
| `qa autostart on` / `off` | Start when you log in |
| `qa log` | Show the end of the log (`~/.quickadd.log`) |

## 🔗 Connect a calendar

Open **Settings** with `/settings` in the popup, or from the tray. The first time you save a password or
sign in, you choose a **passphrase**. It encrypts everything you store, and you type it again after
30 minutes without use (you can change that).

### 🍎 Apple Calendar
1. Go to **appleid.apple.com → Sign-In and Security → App-Specific Passwords** and create one.
   *It's a separate password for apps, not your normal Apple password. You can revoke it any time.*
2. In Settings → **Accounts → Apple Calendar**, enter your Apple ID and that password, press **Connect**,
   choose a calendar and press **Save**.

### 🟢 Google Calendar
Press **Sign in with Google**, choose your account, press **Allow**. That's it.

> The first time, Google may show *"Google hasn't verified this app"*. Click **Advanced → Go to Quick add**.
> This appears once per person until the app is verified by Google.

**Maintainers** (the person who ships the app) must do a one-time setup so that button works.
See [Ship Google sign-in](#-ship-google-sign-in-maintainers-only) below.

## ⌨️ Slash commands

Type `/` in the popup. `Tab` completes, `Enter` runs.

| Command | Does |
|---|---|
| `/settings` | Open the settings window |
| `/status` | Default calendar, vault state, timezone, hotkey |
| `/google` · `/apple` | Make that calendar the default |
| `/target` | Show or set the default |
| `/lock` | Forget the passphrase now |
| `/log` · `/restart` · `/quit` | Open the log, restart, stop |
| `… /google` or `… /apple` after an event | Send just that event there |

## 🔒 Privacy and security

| What | Where | Protection |
|---|---|---|
| Apple app-specific password | `~/.quickadd.vault` | Encrypted with your passphrase (scrypt + AES) |
| Google sign-in token | `~/.quickadd.vault` | Encrypted with your passphrase |
| Apple ID, calendar names, preferences | `~/.quickadd.json` | Plain text, **never** a secret |
| Events | Your calendar provider | Their account security |
| Fallback `.ics` files | `Documents\quickadd` | Plain text (only if saving to a calendar failed) |

- **No server of ours.** The app talks directly to Apple or Google from your PC, and only when you add an event.
- **Nothing is logged** that contains a password or token.
- The passphrase is never stored. After the idle time (default 30 minutes) the app forgets what it unlocked.
- A malicious program already running as *you* could still read memory while the vault is unlocked.
  Encryption protects stolen files and backups, not an already-compromised session.

## 🛠️ Ship Google sign-in (maintainers only)

Google needs to know *which app* is asking. You register that once, and every user shares it. Nothing
runs on a server, and the client ID is not a key to anyone's calendar. Users still have to press Allow.

> ⏱️ About 10 minutes · free · done once. The same steps, with buttons, are in the app:
> **Settings → Accounts → Google → Advanced options → Setup guide.**

1. **Create a project** at [console.cloud.google.com](https://console.cloud.google.com/projectcreate). Name it like your app; users see this name.
2. **Turn on the Calendar API**: [APIs & Services → Library → Google Calendar API → Enable](https://console.cloud.google.com/apis/library/calendar-json.googleapis.com).
3. **Set up the consent screen**: [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent). Choose *External*, add the app name, logo, your support e-mail and the permission `.../auth/calendar.events`.
4. **Create the client**: [Credentials → Create credentials → OAuth client ID](https://console.cloud.google.com/apis/credentials) → application type **Desktop app**. Copy the client ID and secret.
5. **Give it to the app**: copy `google_client.example.json` to `google_client.json` and paste both values.
6. **Publish**: on the consent screen press **Publish app** (*In production*). Otherwise Google ends every sign-in after 7 days.

> **About the "unverified" page.** Until Google reviews the app (a website, a privacy policy, a short demo
> video), users click through one extra warning page. It's cosmetic but looks scary, so mention it in your release notes.
> You can delete or replace the client in the console at any time. Users then just sign in again.

Prefer not to ship anything? Under **Advanced options** a user can use **their own** Google project,
or choose **Open events in my browser**, which needs no sign-in but one extra *Save* click per event.

## 🧰 Troubleshooting

| Problem | Try |
|---|---|
| Hotkey does nothing | Another program may use it. Change it in Settings → General, then `qa restart`. Check `qa log`. |
| "Already running" | `qa status`, then `qa restart`. |
| Events show at the wrong hour | Settings → Defaults → Time. Leave it on automatic, or enter a zone like `Europe/Berlin`. |
| Forgot the passphrase | It can't be recovered (that's the point). Close quick-add, delete `%USERPROFILE%\.quickadd.vault`, then connect your calendars again. Your calendars themselves are not affected. |
| Google says the sign-in expired | The app is still in *Testing* in Google Cloud. Publish it (step 6 above). |
| Nothing happens on Enter | Open `/log`. Failed saves are written to `Documents\quickadd` as `.ics` files instead. |

## 🧪 For developers

```powershell
.venv\Scripts\python eval_calendar.py   # phrase parsing   (52 cases)
.venv\Scripts\python test_backend.py    # vault, settings, Google, commands
.venv\Scripts\python test_ui.py         # the real windows (it shows them briefly; don't type)
```

All tests use temporary files and stubs: they never touch your real settings, vault or calendars.

| File | Role |
|---|---|
| `quickadd.py` | The popup, hotkey, tray wiring |
| `eventparser.py` | Sentence → title, start, end, repeat (uses `ctparse`) |
| `eventout.py` | Event → `.ics` with timezones |
| `calendars.py` · `icloud.py` · `google_cal.py` · `google_link.py` | Where events go |
| `vault.py` · `settings.py` | Encrypted secrets · plain preferences |
| `commands.py` · `settings_window.py` · `theme.py` | Slash commands · settings UI · colours |
| `quickadd_ctl.py` · `singleton.py` · `tray.py` · `qa.bat` | Start/stop, single instance, tray |

## ⚠️ Current limits

Windows only · English phrases only · Apple needs an app-specific password · one extra click if you pick the Google browser option.

## 📄 License

*To be decided.*

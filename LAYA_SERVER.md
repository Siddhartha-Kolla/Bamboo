# Running Laya as Your Own Secure Server

Laya is Python-only. To use it from a website (any language), run it as an HTTP server on a machine you control, and call it from your website's **backend**.

```
Browser  ->  Your backend (holds the secret key)  ->  Laya server (localhost / private)
```

---

## 1. One-time setup

```powershell
cd d:\Python\Laya
.venv\Scripts\activate
pip install fastapi uvicorn      # NOT installed yet in this venv
```

The first request downloads the model checkpoints from Hugging Face (needs internet once). After that they are cached locally.

## 2. Configure

All config is environment variables. Set them in the same terminal before starting (PowerShell):

```powershell
$env:LAYA_HOST    = "127.0.0.1"          # local only (default is 0.0.0.0 = whole network!)
$env:LAYA_PORT    = "8000"
$env:LAYA_API_KEY = "paste-a-long-random-secret"
$env:LAYA_MODELS  = "english"            # load only what you need (saves RAM)
$env:LAYA_PRELOAD = "1"                  # load models at startup, not on first request
```

Generate a strong key:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

| Variable | What it does | Default |
|---|---|---|
| `LAYA_HOST` | Bind address. Use `127.0.0.1` unless a proxy/other machine must reach it | `0.0.0.0` |
| `LAYA_PORT` | Port | `8000` |
| `LAYA_API_KEY` | Require `Authorization: Bearer <key>` on requests | none (open!) |
| `LAYA_MODELS` | Which checkpoints to load: `english`, `multilingual`, `typed-decisions` | all |
| `LAYA_PRELOAD` | Load models at startup | `1` |
| `LAYA_DEVICE` | `cpu`, `cuda`, ... | auto |
| `LAYA_THREADS` | Cap CPU threads (keep <= physical cores) | torch default |
| `LAYA_MAX_CONCURRENT` | Max simultaneous requests; extra get HTTP 503 | `16` |
| `LAYA_MAX_LOADED` | Models kept in memory at once | `2` |
| `LAYA_IDLE_UNLOAD_SECONDS` | Free model memory after idle time | off |
| `LAYA_MAX_TOKEN_BUDGET` | Cap on per-request `max_len` | `8192` |
| `LAYA_ROOT_PATH` | URL prefix when behind a reverse proxy | none |
| `LAYA_LOG_LEVEL` | `debug` / `info` / `warning` | `info` |

## 3. Start

```powershell
laya-serve
# or: python -m laya.serve
```

Check it is up:

```powershell
curl http://127.0.0.1:8000/health
```

Test a real call:

```powershell
curl -X POST http://127.0.0.1:8000/v1/systemone `
  -H "Authorization: Bearer YOUR_KEY" -H "Content-Type: application/json" `
  -d '{"state":"I was charged twice","questions":{"urgent":{"type":"noul","instructions":"Is this urgent?"}}}'
```

## 4. Stop

- Foreground: press `Ctrl+C` in its terminal.
- Background / lost terminal:
  ```powershell
  Get-NetTCPConnection -LocalPort 8000 | Select OwningProcess     # find PID
  Stop-Process -Id <PID>
  ```

## 5. Keep it running (optional)

- **Windows:** Task Scheduler (trigger: at startup, action: `d:\Python\Laya\.venv\Scripts\laya-serve.exe`, with env vars set in a small `.ps1` wrapper) or NSSM to make it a service.
- **Linux VPS:** a systemd unit with `Environment=LAYA_...` lines and `Restart=always`.
- **Docker:** also fine; set the same env vars.

## 6. Security checklist

1. **Always set `LAYA_API_KEY`.** Without it anyone who can reach the port can use your server.
2. **Bind to `127.0.0.1`** if your website backend runs on the same machine. If it's a different machine, firewall the port so only that machine's IP can connect.
3. **Never call Laya from browser JavaScript.** The key would be visible to every visitor. Only your backend should hold it. The server has no CORS support anyway, so browsers can't call it directly.
4. **Put HTTPS in front** if traffic crosses a network (Caddy or nginx reverse proxy with a Let's Encrypt cert). Set `LAYA_ROOT_PATH` if you serve under a prefix.
5. **Keep the key out of code and git.** Use environment variables or your host's secret store. Rotate it by changing `LAYA_API_KEY` and restarting.
6. **Rate-limit at your backend/proxy.** Laya only caps concurrency (`LAYA_MAX_CONCURRENT`), not per-user rate.
7. **Treat input as untrusted.** Laya returns labels/probabilities, not instructions. Still validate your own handling of the results (e.g. don't auto-delete based on one score).
8. **Limits already enforced:** 2 MB body, 50,000 chars of text, 64 questions per request.
9. **Keep updated:** `pip install -U laya` occasionally.

## 7. Calling it from your backend

Node example (server-side only):

```js
const r = await fetch(process.env.LAYA_URL + "/v1/systemone", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "Authorization": "Bearer " + process.env.LAYA_API_KEY,
  },
  body: JSON.stringify({ state: userText, questions }),
});
const { answers } = await r.json();
```

Endpoints:

| Route | Purpose |
|---|---|
| `POST /v1/systemone` | One text, many questions |
| `POST /v1/systemone/batch` | Many texts (`states` + `questions`) |
| `GET /health` | Liveness (details shown only with the key) |

## 8. Troubleshooting

- **401/403:** wrong or missing `Authorization: Bearer` header.
- **503:** over `LAYA_MAX_CONCURRENT`; retry or raise it.
- **500 "inference failed":** check the server terminal log for the real cause.
- **Slow first request:** model download/load. Use `LAYA_PRELOAD=1`.
- **High RAM:** limit `LAYA_MODELS`, set `LAYA_IDLE_UNLOAD_SECONDS`.

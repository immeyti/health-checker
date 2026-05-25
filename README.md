# dashboard-monitor

A self-hosted uptime monitor that checks servers (ICMP ping, TCP port) and web dashboards (full browser login via Playwright), records a video of every check, and sends SMS alerts on state changes.

![Python](https://img.shields.io/badge/python-3.11+-blue) ![License](https://img.shields.io/badge/license-ISC-green)

---

## Features

- **ICMP ping** monitoring with latency tracking
- **TCP port** checks with configurable timeout
- **Dashboard login** checks — drives a real Chromium browser, fills the login form, verifies a success condition (URL change, element, text)
- **Video recording** — every dashboard check saves a `.webm` video; the last video per target is always available in the web UI
- **SQLite storage** — append-only check log with configurable retention (default 30 days)
- **Live web UI** — status table, 24-hour timeline, click-to-play video lightbox
- **REST API** — `/api/status`, `/api/history/{target}` for external consumers
- **SMS alerts** on UP↔DOWN transitions via [Kavenegar](https://kavenegar.com) or [Twilio](https://twilio.com)
- **HTTP Basic Auth** on the web UI
- **Docker-first** — single `docker compose up -d` to run everything

---

## Quick start

### With Docker (recommended)

```bash
# 1. Clone the repo
git clone https://github.com/your-username/dashboard-monitor.git
cd dashboard-monitor

# 2. Set up config and secrets
cp config/config.example.yaml config/config.yaml   # edit with your targets
cp .env.example .env                                # fill in API keys and UI credentials

# 3. Run
docker compose up -d

# 4. Open the dashboard
open http://localhost:8000
```

### Without Docker

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

cp config/config.example.yaml config/config.yaml
cp .env.example .env
# edit both files

python3 -m src.main
```

---

## Configuration

### `config/config.yaml`

```yaml
check_interval: 300   # seconds between full monitoring cycles

storage:
  retention_days: 30

targets:
  ping:
    - name: "My Server"
      host: "1.2.3.4"

  tcp:
    - name: "My Server HTTPS"
      host: "1.2.3.4"
      port: 443
      timeout: 5000   # ms

  dashboards:
    - name: "My Dashboard"
      url: "https://example.com/login"
      username: "admin@example.com"
      password: "${DASHBOARD_PASSWORD}"
      success_indicator:
        type: "url_contains"      # url_contains | element_exists | text_contains
        value: "/dashboard"
      timeout: 15000              # ms

alerts:
  channels:
    - type: "sms"
      provider: "kavenegar"       # kavenegar | twilio
      recipients:
        - "+1234567890"
      kavenegar:
        api_key: "${KAVENEGAR_API_KEY}"
        sender: "YOUR_SENDER_NUMBER"
```

### `.env`

```bash
# Web UI credentials
MONITOR_USERNAME=admin
MONITOR_PASSWORD=changeme

# SMS provider (only the one you use is required)
KAVENEGAR_API_KEY=your_key_here

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_token_here

# Dashboard passwords referenced as ${VAR} in config.yaml
DASHBOARD_PASSWORD=your_dashboard_password
```

`${VAR_NAME}` in `config.yaml` is automatically interpolated from the environment at startup.

---

## REST API

| Endpoint | Description |
|---|---|
| `GET /api/status` | Current status of all targets |
| `GET /api/status/{name}` | Status of a single target |
| `GET /api/history/{name}` | Check log for a target (`?limit=100&offset=0`) |

All endpoints require HTTP Basic Auth with the same credentials as the web UI.

---

## Docker volumes

| Volume | Contents |
|---|---|
| `monitor_data` | SQLite database (`monitor.db`) |
| `monitor_videos` | Latest `.webm` recording per dashboard target |
| `monitor_screenshots` | Screenshots (reserved for future use) |

Config and `.env` are bind-mounted from the host so you can edit them without rebuilding the image.

---

## Project structure

```
src/
  main.py            # entry point — asyncio event loop for daemon + uvicorn
  config.py          # YAML loader with ${ENV_VAR} interpolation
  models.py          # Pydantic models
  state.py           # SQLite StateTracker
  daemon.py          # monitoring loop, concurrent checkers, alert dispatch

  checkers/
    ping.py          # ICMP via asyncio subprocess
    tcp.py           # TCP connect check
    dashboard.py     # Playwright login + video recording

  channels/
    sms/
      kavenegar.py   # Kavenegar HTTP API
      twilio.py      # Twilio REST API

  web/
    app.py           # FastAPI factory
    routes.py        # dashboard + API routes
    templates/       # Jinja2 + Tailwind CSS
```

---

## Running tests

```bash
pip install -r requirements-test.txt
pytest -m "not slow"   # fast suite (~1s)
pytest                  # full suite including real network tests
```

---

## Extending

**Add a new check type** — implement `BaseChecker` in `src/checkers/`, add it to the daemon loop in `src/daemon.py`.

**Add a new alert channel** — implement `BaseChannel` in `src/channels/`, register it in `src/channels/__init__.py:build_channel()`.

**Add a new SMS provider** — implement `BaseSMSProvider` in `src/channels/sms/`, add an `elif` in `SMSChannel.from_config()`.

---

## Roadmap

- [ ] **Alert cooldown** — suppress repeated alerts until the target has been stable for N cycles (avoid alert storms during flapping)
- [ ] **Alert after N failures** — `consecutive_failures` column already tracked in the DB; wire it up in the daemon
- [ ] **Recovery alerts** — send an explicit "back UP" SMS when a target recovers
- [ ] **Email / Telegram / webhook channels** — the channel interface is ready; just needs new implementations
- [ ] **HTTP endpoint checker** — check a URL's HTTP status code without a full browser login (lighter than Playwright for simple APIs)
- [ ] **Prometheus metrics endpoint** — expose check results as Gauge metrics for Grafana dashboards
- [ ] **Config hot-reload** — watch `config.yaml` for changes and apply without a restart
- [ ] **HTTPS for the web UI** — TLS termination, either built-in or documented reverse-proxy setup (nginx/Caddy)
- [ ] **Multi-user / role-based access** — currently single shared credential; add read-only viewer role
- [ ] **Maintenance windows** — suppress alerts for scheduled downtime without stopping the daemon

---

## License

ISC

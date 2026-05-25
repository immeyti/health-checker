# CLAUDE.md — dashboard-monitor

## What this project does

Continuous monitoring daemon that:
- Pings one or more IPs (ICMP via subprocess)
- Logs into dashboard URLs using a headless Playwright browser and records a video of each check
- Stores all check results in SQLite with configurable retention
- Serves a live web UI (FastAPI + Jinja2) at `http://localhost:8000`
- Sends alerts via a channel-based system (SMS first — Kavenegar or Twilio)

---

## How to run

```bash
# Start fresh (wipe previous logs)
rm -f monitor.db && python3 -m src.main

# Normal start
python3 -m src.main

# Test the dashboard checker only (without starting the full app)
python3 test_dashboard.py
```

---

## Project structure

```
src/
  main.py          # entry point — wires daemon + uvicorn in one asyncio event loop
  config.py        # YAML loader with ${ENV_VAR} interpolation → typed AppConfig
  models.py        # Pydantic models: CheckResult, TargetStatus, Alert, CheckLog
  state.py         # SQLite StateTracker — two tables: target_states + check_logs
  daemon.py        # async monitoring loop, concurrent checkers, alert dispatch

  checkers/
    base.py        # abstract BaseChecker
    ping.py        # ICMP via asyncio subprocess
    dashboard.py   # Playwright login check + video recording

  channels/
    base.py        # abstract BaseChannel
    __init__.py    # ChannelFactory — add new channels here
    sms/
      sms_channel.py    # SMSChannel (formats alert, sends to all recipients)
      base_provider.py  # abstract BaseSMSProvider
      kavenegar.py      # Kavenegar HTTP API
      twilio.py         # Twilio REST API

  web/
    app.py         # FastAPI factory — mounts /static, /screenshots, /videos static dirs
    routes.py      # GET /, GET /api/status, GET /api/history/{target_name}
    templates/
      base.html
      dashboard.html   # status table with 24h timeline + video thumbnail + lightbox
    static/
      input.css    # Tailwind v4 entry point (@import "tailwindcss")
      tailwind.css # pre-built minified CSS — commit this, rebuild when templates change

config/config.yaml   # targets, intervals, alert channels, storage settings
.env                 # secrets (never commit) — loaded automatically at startup
test_dashboard.py    # standalone test for the dashboard checker + video assertions
package.json         # npm devDeps: tailwindcss + @tailwindcss/cli for local CSS build
tailwind.config.js   # Tailwind config — scans src/web/templates/**/*.html
```

---

## Configuration

Edit `config/config.yaml`. Secrets go in `.env` and are referenced as `${VAR_NAME}`.

```yaml
check_interval: 60        # seconds between cycles
storage:
  retention_days: 30      # auto-purge check_logs older than this
  screenshots_dir: screenshots   # videos go in ../videos/ relative to this

targets:
  ping:
    - name: "My Server"
      host: "1.2.3.4"
  dashboards:
    - name: "My Dashboard"
      url: "https://example.com/login"
      username: "admin"
      password: "${MY_DASHBOARD_PASSWORD}"
      success_indicator:
        type: "url_contains"    # url_contains | element_exists | text_contains
        value: "/dashboard"
      timeout: 15000

alerts:
  channels:
    - type: "sms"
      provider: "kavenegar"   # kavenegar | twilio
      recipients: ["+989..."]
      kavenegar:
        api_key: "${KAVENEGAR_API_KEY}"
        sender: "10004346"
```

---

## Key architecture decisions

### State (SQLite — `state.py`)
- `target_states` — one row per target, upserted each cycle (current status)
- `check_logs` — append-only history, purged after `retention_days`
- **No alert on first check** — alert only on UP↔DOWN transition
- `consecutive_failures` column available for future "alert after N failures" logic

### Dashboard checker (`checkers/dashboard.py`)
- Reuses a long-lived browser process per checker; fresh context per check (clean cookies)
- `slow_mo=300ms` + explicit `asyncio.sleep` pauses between steps → video is human-readable
- Video path stored in `screenshot_path` field (reused for both screenshot and video)
- `SLOW_MO_MS` and `STEP_PAUSE` constants at top of file to tune recording speed
- Login form fields detected via priority-ordered selector lists (no hardcoding)
- Waits for URL change after submit before verifying success — fixes timing race

### Channels (`channels/`)
- `BaseChannel.send(Alert)` is the only interface
- Add a new channel: create a class implementing `BaseChannel`, add `elif` in `channels/__init__.py:build_channel()`
- Add a new SMS provider: implement `BaseSMSProvider`, add `elif` in `sms_channel.py:SMSChannel.from_config()`

### Video recording
- Playwright records `.webm` per check into `videos/`
- UUID filename is renamed to `{safe_target_name}.webm` after context closes (overwrites previous)
- Served at `/videos/{filename}.webm` via FastAPI `StaticFiles`
- Dashboard shows thumbnail + click-to-play lightbox

---

## Database

```sql
-- current status per target
target_states: name, target_type, current_status, previous_status,
               last_checked, latency_ms, consecutive_failures, first_seen, screenshot_path

-- full history (append-only, auto-purged)
check_logs: id, target_name, target_type, status, latency_ms,
            checked_at, error_message, screenshot_path
```

Schema migrations are handled in `StateTracker._migrate_db()` via `ALTER TABLE ... ADD COLUMN` (safe to run on every startup — catches `OperationalError` if column exists).

---

## Adding a new feature — recommended workflow

1. **Use `/plan` for anything touching multiple files** — Claude will read the code first and propose an approach before changing anything
2. **Always run `pytest -m "not slow"` before making any edits** — establish a clean baseline
3. **Always run `pytest -m "not slow"` again after every change** — confirm nothing broke
3. **Test in isolation first** with `test_dashboard.py` before running the full daemon
4. **Never hardcode secrets** — always use `${ENV_VAR}` in config and `.env` for values
5. **Channel-based pattern** — alerts, checker types, and SMS providers are all designed to be extended without modifying existing files

---

## Running tests

```bash
# Run all fast tests (recommended before every commit)
pytest -m "not slow"

# Run the full suite including the ~5s network test
pytest

# Run a single file
pytest tests/test_state.py -v

# Run with verbose output
pytest -v

# Run only tests matching a keyword
pytest -k "transition"
```

### Test layout

| File | What it covers |
|---|---|
| `tests/test_state.py` | StateTracker — transitions, uptime, timeline, purge (13 cases) |
| `tests/test_ping.py` | PingChecker — real subprocess ping (1 fast + 1 slow) |
| `tests/test_dashboard_checker.py` | DashboardChecker — subclass mocking, no Playwright (2 cases) |
| `tests/test_channels.py` | SMSChannel + build_channel (4 cases) |
| `tests/test_daemon.py` | MonitoringDaemon._run_cycle — alert dispatch, broken channel (3 cases) |
| `tests/test_web.py` | FastAPI routes via TestClient (8 cases) |

### Testing rules (from CLAUDE.md)
- Use real SQLite (`:memory:`) — never mock the DB
- Mock checkers/channels by subclassing, not `unittest.mock.patch`
- `pytest-asyncio` with `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed
- `@pytest.mark.slow` on tests that do real network I/O (ping unreachable host)

---

## Common tasks

| Task | Command |
|---|---|
| Reset all logs and start fresh | `rm -f monitor.db && python3 -m src.main` |
| Run tests (fast) | `pytest -m "not slow"` |
| Run all tests | `pytest` |
| Test dashboard checker + video | `python3 test_dashboard.py` |
| Check current status (JSON) | `curl http://localhost:8000/api/status` |
| Watch last check video | `open videos/<target_name>.webm` |
| View check history | `curl http://localhost:8000/api/history/<target_name>` |
| Add or change a target | Edit `config/config.yaml` then restart the app — config is loaded once at startup |

---

## What NOT to do

- Do not commit `.env` or `monitor.db` — both are in `.gitignore`
- Do not add error handling for impossible cases — trust Pydantic and SQLite
- Do not mock the database in tests — use the real SQLite (in-memory or temp file)
- Do not add backwards-compatibility shims — just change the code
- Do not modify `slow_mo` or `STEP_PAUSE` without re-running `test_dashboard.py` to verify the video is still readable

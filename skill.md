# AutoGLM Local REST Skill (for Agents)

This document is a lightweight “skill” spec for Claude Code / Codex / OpenCode-style agents.
It describes how to call the local AutoGLM HTTP server via REST/SSE to execute a natural-language task on an Android device.

Scope:
- Android/ADB only
- One request = agent plans + executes on device + returns result
- Streaming output supported (recommended)

Non-goals:
- No code changes required in the caller
- No MCP required (you can wrap this HTTP API as an MCP tool later if desired)

## Preconditions

1) Start the AutoGLM server locally (it reads `Open-AutoGLM/.env` automatically):

```bash
cd Open-AutoGLM
python main.py --serve --host 127.0.0.1 --port 9090
```

2) Ensure `Open-AutoGLM/.env` has at least:

```bash
PHONE_AGENT_BASE_URL="http://localhost:8045/v1"
PHONE_AGENT_MODEL="gemini-3-flash-preview"
PHONE_AGENT_API_KEY="EMPTY"
```

Optional:
- `Open-AutoGLM/memory.json` will be loaded by server mode by default if present
- `PHONE_AGENT_DEVICE_ID` if multiple Android devices are connected

3) Ensure `adb devices` has at least one device connected

## API Overview

Base URL (default): `http://127.0.0.1:9090`

Endpoints:
- `GET /health` -> JSON health status
- `POST /run` -> JSON response after completion
- `POST /run/stream` -> SSE stream of logs + final result (recommended)

Auth (optional):
- If server started with `--http-token XXX` or env `PHONE_AGENT_HTTP_TOKEN=XXX`
- Add header: `Authorization: Bearer XXX`

## Request Schema (POST /run and /run/stream)

Minimal:

```json
{ "task": "Open Mixin，Send a message 'Hell0 wOrld' to 28865" }
```
```
{ "task": "Open Mixin，Send a 0.01 SHIB to 28865" }
```

Optional fields (all are safe to omit):
- `device_id`: string
- `lang`: `"cn" | "en"`
- `max_steps`: number
- `batch_actions`: boolean
- `batch_size`: number
- `auto_confirm_sensitive`: boolean
- `include_logs`: boolean (only used by `/run`)
- `memory_file`: string path (override server default)

Notes:
- In server mode the defaults are `batch_actions=true` and `auto_confirm_sensitive=true`.
- Use `batch_size=6` for predictable multi-tap sequences (PIN/OTP keypad).

## Response Schema (POST /run)

Success:

```json
{
  "ok": true,
  "result": "...",
  "elapsed_s": 12.34,
  "step_count": 5
}
```

Failure:

```json
{
  "ok": false,
  "error": "...",
  "elapsed_s": 3.21,
  "traceback": "...",
  "logs": "..."
}
```

## Streaming via SSE (POST /run/stream) (Recommended)

SSE produces a sequence of events until a terminal event is received:
- `event: server` -> one-line server lifecycle logs (CONNECT/REQUEST/MODEL/START)
- `event: output` -> aggregated model/agent output chunks (human-readable)
- `event: result` -> final JSON object (`ok=true`)
- `event: error` -> final JSON object (`ok=false`)

The stream may also include keepalive lines starting with `:`.

### Cancellation semantics

To cancel a running task:
- Close the SSE connection (e.g., Ctrl+C in curl).

Server behavior:
- On client disconnect, the server terminates the worker process and stops executing further steps.

## Examples

### Health

```bash
curl http://127.0.0.1:9090/health
```

### Run (blocking)

```bash
curl -X POST http://127.0.0.1:9090/run \
  -H 'Content-Type: application/json' \
  -d '{"task":"Open Mixin，Send a message 'Hell0 wOrld' to 28865"}'
```

### Run (streaming)

```bash
curl -N -X POST http://127.0.0.1:9090/run/stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"Open Mixin，Send a message 'Hell0 wOrld' to 28865"}'
```

### PIN/OTP (predictable multi-tap)

```bash
curl -N -X POST http://127.0.0.1:9090/run/stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"在 PIN 输入界面输入 123456 并确认","batch_actions":true,"batch_size":6}'
```

### Python client (minimal SSE reader)

```python
import json
import requests

url = "http://127.0.0.1:9090/run/stream"
payload = {"task": "Open Chrome and search for nearby coffee"}

with requests.post(url, json=payload, stream=True, timeout=30) as r:
    r.raise_for_status()
    event = None
    for raw in r.iter_lines(decode_unicode=True):
        if not raw:
            continue
        if raw.startswith(":"):
            continue
        if raw.startswith("event: "):
            event = raw.split(": ", 1)[1].strip()
            continue
        if raw.startswith("data: "):
            data = raw.split(": ", 1)[1]
            if event in ("result", "error"):
                print(event, json.loads(data))
                break
            else:
                print(event, data)
```

## Agent integration guidelines

Recommended control loop:
1) Prefer `/run/stream`
2) Print `server` events as single-line status
3) Stream `output` to the user for transparency
4) Stop when `result` or `error` arrives
5) To abort: close the HTTP connection (server will cancel processing)

Reliability tips:
- Enforce an idle timeout (e.g., if no SSE line for 30-60s, disconnect and retry)
- Use `device_id` explicitly if you have multiple devices connected
- If using auth token, always send `Authorization: Bearer ...`

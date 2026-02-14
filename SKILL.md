---
name: autoglm-local-rest
description: Call a local Open-AutoGLM HTTP server via REST/SSE to execute natural-language tasks on an Android device (ADB).
user-invocable: true
version: 1.0.0
---

# AutoGLM Local REST

Use this skill when you need a reliable way to run a single natural-language task against an Android device through a locally running Open-AutoGLM server.

Key properties:

- Android + ADB only
- One request = the server plans + executes on device + returns a result
- Streaming output supported and preferred (`/run/stream`)

## When to Use

- Drive phone UI flows from a non-UI agent by sending one task string
- Get streaming progress logs during long multi-step flows
- Quickly reproduce/triage phone automation behavior via curl or a tiny client

## Tool Target

This skill targets the Open-AutoGLM "server mode" HTTP API (started with `python main.py --serve ...`).

Quick verify:

```bash
curl -s http://127.0.0.1:9090/health
curl -s -X POST http://127.0.0.1:9090/run -H 'Content-Type: application/json' -d '{"task":"Open Settings"}'
```

## Setup and Preconditions

### 1) Start the local server

The server reads `Open-AutoGLM/.env` automatically.

```bash
cd Open-AutoGLM
python main.py --serve --host 127.0.0.1 --port 9090
```

### 2) Ensure `Open-AutoGLM/.env` has at least

```bash
PHONE_AGENT_BASE_URL="http://localhost:8045/v1"
PHONE_AGENT_MODEL="gemini-3-flash-preview"
PHONE_AGENT_API_KEY="EMPTY"
```

Optional:

- `Open-AutoGLM/memory.json` is loaded by server mode by default (if present)
- `PHONE_AGENT_DEVICE_ID` if multiple Android devices are connected

### 3) Ensure ADB sees a device

```bash
adb devices
```

## API Overview

Base URL (default): `http://127.0.0.1:9090`

Endpoints:

- `GET /health`: JSON health status
- `POST /run`: blocks and returns final JSON
- `POST /run/stream`: Server-Sent Events (SSE) stream of logs + terminal result (recommended)

Auth (optional):

- If started with `--http-token <TOKEN>` or env `PHONE_AGENT_HTTP_TOKEN=<TOKEN>`
- Send header: `Authorization: Bearer <TOKEN>`

## Request Schema (`POST /run` and `POST /run/stream`)

Minimal body:

```json
{ "task": "Open Mixin, send a message \"Hell0 wOrld\" to 28865" }
```

Optional fields (safe to omit):

- `device_id`: string
- `lang`: `"cn" | "en"`
- `max_steps`: number
- `batch_actions`: boolean
- `batch_size`: number
- `auto_confirm_sensitive`: boolean
- `include_logs`: boolean (only used by `/run`)
- `memory_file`: string path (override server default)

Notes:

- In server mode defaults are typically `batch_actions=true` and `auto_confirm_sensitive=true`.
- Use `batch_size=6` for predictable multi-tap sequences (PIN/OTP keypad).

## Response Schema (`POST /run`)

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

## Streaming via SSE (`POST /run/stream`) (Recommended)

The stream emits events until a terminal event is received:

- `event: server`: one-line server lifecycle logs (CONNECT/REQUEST/MODEL/START)
- `event: output`: aggregated model/agent output chunks (human-readable)
- `event: result`: final JSON object (`ok=true`)
- `event: error`: final JSON object (`ok=false`)

The stream may also include keepalive lines starting with `:`.

### Cancellation semantics

To cancel a running task: close the SSE connection (for example Ctrl+C in curl).

Server behavior: on client disconnect the server terminates the worker process and stops executing further steps.

## Examples

### Health

```bash
curl http://127.0.0.1:9090/health
```

### Run (blocking)

```bash
curl -X POST http://127.0.0.1:9090/run \
  -H 'Content-Type: application/json' \
  -d '{"task":"Open Mixin, send a message \\\"Hell0 wOrld\\\" to 28865"}'
```

### Run (streaming)

```bash
curl -N -X POST http://127.0.0.1:9090/run/stream \
  -H 'Content-Type: application/json' \
  -d '{"task":"Open Mixin, send a message \\\"Hell0 wOrld\\\" to 28865"}'
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
            print(event, data)
```

## Agent Integration Guidelines

Recommended control loop:

1. Prefer `/run/stream`
2. Print `server` events as single-line status
3. Stream `output` for transparency
4. Stop when `result` or `error` arrives
5. Abort by closing the HTTP connection (server cancels)

Reliability tips:

- Enforce an idle timeout (if no SSE line for 30-60s, disconnect and retry)
- Use `device_id` explicitly if multiple devices are connected
- If auth is enabled, always send `Authorization: Bearer <TOKEN>`

## Security Rules (Mandatory)

- Never commit API keys, HTTP tokens, or any `Open-AutoGLM/.env` contents.
- Avoid pasting SSE logs that include sensitive UI content.
- Prefer passing secrets via environment variables or stdin in CI.

## Troubleshooting

### `401 Unauthorized`

- Confirm the server is started with `--http-token` or `PHONE_AGENT_HTTP_TOKEN`.
- Ensure the caller sends `Authorization: Bearer <TOKEN>`.

### No device / wrong device

- Run `adb devices` and confirm at least one device is in `device` state.
- If multiple devices are connected, set `device_id` (request body) or `PHONE_AGENT_DEVICE_ID` (env).

### Streaming stalls

- Add client-side idle timeouts and retry.
- Verify the server process is still alive and not blocked on a takeover/prompt.

## References

- https://github.com/zai-org/Open-AutoGLM

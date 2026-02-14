import io
import json
import subprocess
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from phone_agent import PhoneAgent
from phone_agent.agent import AgentConfig
from phone_agent.config import get_system_prompt
from phone_agent.device_factory import DeviceType, set_device_type
from phone_agent.model import ModelConfig


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _sse_send(handler: BaseHTTPRequestHandler, event: str, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False)
    msg = f"event: {event}\ndata: {payload}\n\n"
    handler.wfile.write(msg.encode("utf-8"))
    handler.wfile.flush()


def _sse_send_text(handler: BaseHTTPRequestHandler, event: str, text: str) -> None:
    handler.wfile.write(f"event: {event}\n".encode("utf-8"))
    for line in text.splitlines():
        handler.wfile.write(f"data: {line}\n".encode("utf-8"))
    handler.wfile.write(b"\n")
    handler.wfile.flush()


def _is_sep_line(line: str) -> bool:
    s = line.strip("\r\n")
    return len(s) >= 16 and (set(s) == {"-"} or set(s) == {"="})


def _compact_sep_line(line: str) -> str:
    s = line.strip("\r\n")
    if set(s) == {"-"}:
        return "----\n"
    if set(s) == {"="}:
        return "====\n"
    return line


def _summarize_action(action: dict[str, Any]) -> str:
    name = action.get("action")
    if name == "Tap":
        el = action.get("element")
        if isinstance(el, list) and len(el) == 2:
            return f"ACTION Tap element={el}\n"
        return "ACTION Tap\n"
    if name == "Swipe":
        start = action.get("start")
        end = action.get("end")
        if isinstance(start, list) and isinstance(end, list):
            return f"ACTION Swipe start={start} end={end}\n"
        return "ACTION Swipe\n"
    if name == "Type":
        text = action.get("text")
        if isinstance(text, str):
            short = text if len(text) <= 40 else (text[:37] + "...")
            return f"ACTION Type text={short!r}\n"
        return "ACTION Type\n"
    if isinstance(name, str) and name:
        return f"ACTION {name}\n"
    return "ACTION\n"


def _server_log(message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {message}", flush=True)


def _build_system_prompt(lang: str, batch_actions: bool, batch_size: int) -> str:
    return _build_system_prompt_with_memory(lang, None, batch_actions, batch_size)


def _build_system_prompt_with_memory(
    lang: str,
    memory_file: str | None,
    batch_actions: bool,
    batch_size: int,
) -> str:
    prompt = get_system_prompt(lang)

    if memory_file:
        try:
            with open(memory_file, encoding="utf-8") as f:
                raw = f.read().strip()
            if raw:
                if memory_file.endswith(".json"):
                    memory_obj = json.loads(raw)
                    memory_text = json.dumps(memory_obj, ensure_ascii=False, indent=2)
                else:
                    memory_text = raw

                prompt = (
                    prompt
                    + "\n\n[Persistent Memory]\nUse these stable user preferences/facts when relevant.\n"
                    + memory_text
                )
        except Exception:
            pass

    if batch_actions:
        prompt += (
            "\n\n[Batch Action Mode]\n"
            f"When helpful, output up to {max(1, batch_size)} actions in <answer>, one action per line.\n"
            "Each line must be do(...) or finish(...).\n"
            "Avoid Interact unless user input is truly required."
        )

    return prompt


def serve(
    host: str,
    port: int,
    base_url: str,
    model: str,
    api_key: str,
    device_id: str | None,
    lang: str,
    max_steps: int,
    batch_actions: bool,
    batch_size: int,
    memory_file: str | None = None,
    auto_confirm_sensitive_default: bool = False,
    auth_token: str | None = None,
):
    set_device_type(DeviceType.ADB)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args):
            return

        def _require_auth(self) -> bool:
            if not auth_token:
                return True
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer ") and header.removeprefix("Bearer ").strip() == auth_token:
                return True
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return False

        def do_GET(self):
            if self.path.rstrip("/") == "/health":
                if not self._require_auth():
                    return
                _server_log(f"GET /health from {self.client_address[0]}")
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "status": "ok",
                        "device_type": "adb",
                        "time": time.time(),
                    },
                )
                return

            _json_response(self, 404, {"ok": False, "error": "not_found"})

        def do_POST(self):
            path = self.path.rstrip("/")
            if path not in ("/run", "/run/stream"):
                _json_response(self, 404, {"ok": False, "error": "not_found"})
                return

            if not self._require_auth():
                return

            try:
                payload = _read_json(self)
            except Exception:
                _json_response(self, 400, {"ok": False, "error": "invalid_json"})
                return

            task = (payload.get("task") or "").strip()
            if not task:
                _json_response(self, 400, {"ok": False, "error": "missing_task"})
                return

            req_device_id = payload.get("device_id") or device_id
            lock_key = req_device_id or "default"
            lock = _get_lock(lock_key)

            run_lang = payload.get("lang") or lang
            run_max_steps = int(payload.get("max_steps") or max_steps)
            run_batch_actions = bool(
                payload.get("batch_actions")
                if "batch_actions" in payload
                else batch_actions
            )
            run_batch_size = int(payload.get("batch_size") or batch_size)
            run_memory_file = payload.get("memory_file") or memory_file
            run_base_url = payload.get("base_url") or base_url
            run_model = payload.get("model") or model
            run_api_key = payload.get("api_key") or api_key

            auto_confirm_sensitive = (
                bool(payload.get("auto_confirm_sensitive"))
                if "auto_confirm_sensitive" in payload
                else auto_confirm_sensitive_default
            )

            model_config = ModelConfig(
                base_url=run_base_url,
                model_name=run_model,
                api_key=run_api_key,
                lang=run_lang,
            )
            agent_config = AgentConfig(
                max_steps=run_max_steps,
                device_id=req_device_id,
                verbose=False,
                lang=run_lang,
                system_prompt=_build_system_prompt_with_memory(
                    run_lang,
                    run_memory_file,
                    run_batch_actions,
                    run_batch_size,
                ),
                batch_actions=run_batch_actions,
                batch_size=run_batch_size,
            )

            def confirmation_callback(_message: str) -> bool:
                return auto_confirm_sensitive

            def takeover_callback(message: str) -> None:
                raise RuntimeError(f"takeover_required: {message}")

            agent = PhoneAgent(
                model_config=model_config,
                agent_config=agent_config,
                confirmation_callback=confirmation_callback,
                takeover_callback=takeover_callback,
            )

            started = time.time()

            task_short = task.replace("\n", " ")
            if len(task_short) > 160:
                task_short = task_short[:160] + "..."
            _server_log(
                f"POST {path} from {self.client_address[0]} device_id={req_device_id or '-'} task={task_short}"
            )

            if path == "/run":
                with lock:
                    buf = io.StringIO()
                    try:
                        with redirect_stdout(buf), redirect_stderr(buf):
                            result = agent.run(task)

                        elapsed = time.time() - started
                        include_logs = bool(payload.get("include_logs"))
                        response: dict[str, Any] = {
                            "ok": True,
                            "result": result,
                            "elapsed_s": elapsed,
                            "step_count": agent.step_count,
                        }
                        if include_logs:
                            response["logs"] = buf.getvalue()[-20000:]
                        _json_response(self, 200, response)
                        _server_log(
                            f"DONE /run ok elapsed_s={elapsed:.3f} steps={agent.step_count}"
                        )
                    except Exception as e:
                        elapsed = time.time() - started
                        logs = buf.getvalue()[-20000:]
                        _json_response(
                            self,
                            500,
                            {
                                "ok": False,
                                "error": str(e),
                                "elapsed_s": elapsed,
                                "traceback": traceback.format_exc()[-20000:],
                                "logs": logs,
                            },
                        )
                        _server_log(f"DONE /run error elapsed_s={elapsed:.3f} err={e}")
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            worker_payload = {
                "task": task,
                "device_id": req_device_id,
                "lang": run_lang,
                "max_steps": run_max_steps,
                "base_url": run_base_url,
                "model": run_model,
                "api_key": run_api_key,
                "batch_actions": run_batch_actions,
                "batch_size": run_batch_size,
                "memory_file": run_memory_file,
                "auto_confirm_sensitive": auto_confirm_sensitive,
                "dry_run": bool(payload.get("dry_run")),
                "dry_run_seconds": payload.get("dry_run_seconds"),
            }

            with lock:
                try:
                    _sse_send_text(
                        self,
                        "server",
                        f"CONNECT from={self.client_address[0]} device_id={req_device_id or '-'}",
                    )
                    _sse_send_text(
                        self,
                        "server",
                        f"REQUEST task={task_short}",
                    )
                    _sse_send_text(
                        self,
                        "server",
                        f"MODEL name={run_model}",
                    )
                except BrokenPipeError:
                    return

                proc = subprocess.Popen(
                    [sys.executable, "-m", "phone_agent.http_worker"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )

                assert proc.stdin is not None
                assert proc.stdout is not None
                proc.stdin.write(json.dumps(worker_payload, ensure_ascii=False))
                proc.stdin.close()

                _server_log(
                    f"START /run/stream pid={proc.pid} device_id={req_device_id or '-'}"
                )

                try:
                    _sse_send_text(self, "server", f"START pid={proc.pid}")
                except BrokenPipeError:
                    _server_log(
                        f"CANCEL /run/stream client_disconnected pid={proc.pid}"
                    )
                    proc.terminate()
                    return

                prefix = "__HTTP_WORKER_RESULT__"
                try:
                    pending: list[str] = []
                    pending_chars = 0
                    last_flush = time.time()
                    expecting_action_json = False
                    collecting_action_json = False
                    action_json_lines: list[str] = []

                    def flush_pending() -> None:
                        nonlocal pending, pending_chars, last_flush
                        if not pending:
                            return
                        chunk = "".join(pending)
                        pending = []
                        pending_chars = 0
                        last_flush = time.time()
                        _sse_send_text(self, "output", chunk.rstrip("\n"))

                    last_keepalive = time.time()
                    for line in proc.stdout:
                        if collecting_action_json:
                            action_json_lines.append(line)
                            joined = "".join(action_json_lines)
                            parsed: dict[str, Any] | None
                            try:
                                obj = json.loads(joined)
                                parsed = obj if isinstance(obj, dict) else None
                            except Exception:
                                parsed = None

                            if parsed is not None and "_metadata" in parsed:
                                collecting_action_json = False
                                action_json_lines = []
                                pending.append(_summarize_action(parsed))
                                pending_chars += len(pending[-1])
                                try:
                                    flush_pending()
                                except BrokenPipeError:
                                    _server_log(
                                        f"CANCEL /run/stream client_disconnected pid={proc.pid}"
                                    )
                                    proc.terminate()
                                    return
                                continue

                            if len(joined) > 20000:
                                collecting_action_json = False
                                action_json_lines = []

                        now = time.time()
                        if now - last_keepalive > 10:
                            try:
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                            except BrokenPipeError:
                                _server_log(
                                    f"CANCEL /run/stream client_disconnected pid={proc.pid}"
                                )
                                proc.terminate()
                                return
                            last_keepalive = now

                        if line.startswith(prefix):
                            flush_pending()
                            raw_json = line[len(prefix) :].strip()
                            try:
                                final = json.loads(raw_json) if raw_json else {}
                            except Exception:
                                final = {
                                    "ok": False,
                                    "error": "invalid_worker_result",
                                    "raw": raw_json[-2000:],
                                }

                            ev = "result" if final.get("ok") else "error"
                            _sse_send(self, ev, final)
                            elapsed = final.get("elapsed_s")
                            if isinstance(elapsed, (int, float)):
                                _server_log(
                                    f"DONE /run/stream {ev} elapsed_s={elapsed:.3f} pid={proc.pid}"
                                )
                            else:
                                _server_log(f"DONE /run/stream {ev} pid={proc.pid}")
                            break

                        if "🎯" in line and ("执行动作" in line or "action" in line.lower()):
                            expecting_action_json = True
                            continue

                        if expecting_action_json and line.lstrip().startswith("{"):
                            expecting_action_json = False
                            collecting_action_json = True
                            action_json_lines = [line]
                            continue

                        if _is_sep_line(line):
                            line = _compact_sep_line(line)

                        pending.append(line)
                        pending_chars += len(line)
                        if (
                            pending_chars >= 4096
                            or (now - last_flush) >= 0.25
                            or line.strip() == ""
                            or line.startswith("=")
                            or line.startswith("-")
                        ):
                            try:
                                flush_pending()
                            except BrokenPipeError:
                                _server_log(
                                    f"CANCEL /run/stream client_disconnected pid={proc.pid}"
                                )
                                proc.terminate()
                                return

                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()

                finally:
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                    except Exception:
                        pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()

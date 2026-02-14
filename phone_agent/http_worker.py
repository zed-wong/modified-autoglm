import json
import os
import sys
import time
import traceback

from phone_agent import PhoneAgent
from phone_agent.agent import AgentConfig
from phone_agent.config import get_system_prompt
from phone_agent.device_factory import DeviceType, set_device_type
from phone_agent.model import ModelConfig


_RESULT_PREFIX = "__HTTP_WORKER_RESULT__"


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


def main() -> int:
    set_device_type(DeviceType.ADB)

    raw = sys.stdin.read().strip()
    payload = json.loads(raw) if raw else {}

    task = (payload.get("task") or "").strip()
    if not task:
        sys.stdout.write(
            _RESULT_PREFIX
            + " "
            + json.dumps({"ok": False, "error": "missing_task"}, ensure_ascii=False)
            + "\n"
        )
        sys.stdout.flush()
        return 2

    started = time.time()
    try:
        if bool(payload.get("dry_run")):
            seconds = float(payload.get("dry_run_seconds") or 2.0)
            end = time.time() + max(0.0, seconds)
            while time.time() < end:
                print("dry_run: working...", flush=True)
                time.sleep(0.1)

            elapsed = time.time() - started
            out = {
                "ok": True,
                "result": "dry_run_ok",
                "elapsed_s": elapsed,
                "step_count": 0,
            }
            sys.stdout.write(
                _RESULT_PREFIX + " " + json.dumps(out, ensure_ascii=False) + "\n"
            )
            sys.stdout.flush()
            return 0

        lang = payload.get("lang") or os.getenv("PHONE_AGENT_LANG", "cn")
        max_steps = int(payload.get("max_steps") or os.getenv("PHONE_AGENT_MAX_STEPS", "100"))
        device_id = payload.get("device_id") or os.getenv("PHONE_AGENT_DEVICE_ID")

        base_url = payload.get("base_url") or os.getenv(
            "PHONE_AGENT_BASE_URL", "http://localhost:8000/v1"
        )
        model = payload.get("model") or os.getenv("PHONE_AGENT_MODEL", "autoglm-phone-9b")
        api_key = payload.get("api_key") or os.getenv("PHONE_AGENT_API_KEY", "EMPTY")

        batch_actions = bool(
            payload.get("batch_actions")
            if "batch_actions" in payload
            else os.getenv("PHONE_AGENT_BATCH_ACTIONS", "").lower() in ("1", "true", "yes")
        )
        batch_size = int(payload.get("batch_size") or os.getenv("PHONE_AGENT_BATCH_SIZE", "3"))

        memory_file = payload.get("memory_file")
        if not memory_file:
            candidate = os.path.join(os.getcwd(), "memory.json")
            if os.path.isfile(candidate):
                memory_file = candidate

        auto_confirm_sensitive = bool(payload.get("auto_confirm_sensitive"))

        model_config = ModelConfig(
            base_url=base_url,
            model_name=model,
            api_key=api_key,
            lang=lang,
        )
        agent_config = AgentConfig(
            max_steps=max_steps,
            device_id=device_id,
            verbose=True,
            lang=lang,
            system_prompt=_build_system_prompt_with_memory(
                lang,
                memory_file,
                batch_actions,
                batch_size,
            ),
            batch_actions=batch_actions,
            batch_size=batch_size,
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

        result = agent.run(task)
        elapsed = time.time() - started
        out = {
            "ok": True,
            "result": result,
            "elapsed_s": elapsed,
            "step_count": agent.step_count,
        }
        sys.stdout.write(_RESULT_PREFIX + " " + json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 0
    except Exception as e:
        elapsed = time.time() - started
        out = {
            "ok": False,
            "error": str(e),
            "elapsed_s": elapsed,
            "traceback": traceback.format_exc()[-20000:],
        }
        sys.stdout.write(_RESULT_PREFIX + " " + json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

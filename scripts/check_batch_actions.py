import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from phone_agent.actions.handler import parse_actions
from phone_agent.model.client import ModelClient


def main() -> None:
    raw = """
<think>
Entering PIN on a keypad; UI changes are predictable.
</think>
<answer>
do(action=\"Tap\", element=[100,800])
do(action=\"Tap\", element=[200,800])
do(action=\"Tap\", element=[300,800])
do(action=\"Tap\", element=[400,800])
do(action=\"Tap\", element=[500,800])
do(action=\"Tap\", element=[600,800])
</answer>
""".strip()

    client = ModelClient()
    thinking, action_block = client._parse_response(raw)
    assert thinking, "expected non-empty thinking"
    assert "do(" in action_block, "expected do(...) in action block"

    actions = parse_actions(action_block, max_actions=10)
    assert len(actions) == 6, f"expected 6 actions, got {len(actions)}"
    assert all(a.get("action") == "Tap" for a in actions)
    print("OK: extracted", len(actions), "actions")


if __name__ == "__main__":
    main()

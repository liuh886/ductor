#!/usr/bin/env python3
"""Send an async task to another agent via the InterAgentBus.

Unlike ask_agent.py, this returns immediately with a task_id.
The sub-agent's response is delivered back to YOUR Telegram chat
(the calling agent's chat) when ready — NOT to the sub-agent's chat.

The response ALWAYS comes back to YOU (the calling agent). There is no way
to make the sub-agent reply in its own Telegram chat via this tool.

Uses the internal localhost HTTP API to communicate with the bus.
Environment variables DUCTOR_AGENT_NAME, DUCTOR_INTERAGENT_PORT, and
DUCTOR_INTERAGENT_HOST are automatically set by the Ductor framework.

Usage:
    python3 ask_agent_async.py [--new] TARGET_AGENT "Your message here"

Options:
    --new   Start a fresh session, discarding any prior inter-agent context
            with the recipient. Without this flag, the recipient resumes
            the existing session (if any).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> None:
    args = sys.argv[1:]
    new_session = False
    if args and args[0] == "--new":
        new_session = True
        args = args[1:]

    if len(args) < 2:
        print('Usage: python3 ask_agent_async.py [--new] TARGET_AGENT "message"', file=sys.stderr)
        sys.exit(1)

    target = args[0]
    message = args[1]
    port = os.environ.get("DUCTOR_INTERAGENT_PORT", "8799")
    host = os.environ.get("DUCTOR_INTERAGENT_HOST", "127.0.0.1")
    sender = os.environ.get("DUCTOR_AGENT_NAME", "unknown")

    url = f"http://{host}:{port}/interagent/send_async"
    body: dict[str, object] = {"from": sender, "to": target, "message": message}
    if new_session:
        body["new_session"] = True
    payload = json.dumps(body).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"Error: Cannot reach inter-agent API at {url}: {e}", file=sys.stderr)
        print(
            "Make sure the Ductor supervisor is running with multi-agent support.", file=sys.stderr
        )
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if result.get("success"):
        task_id = result.get("task_id", "unknown")
        print(
            f"Async task sent to '{target}' (task_id: {task_id}). "
            f"The response will be delivered back to your chat when ready."
        )
    else:
        error = result.get("error", "Unknown error")
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

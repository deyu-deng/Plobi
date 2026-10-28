"""A stand-in ACP agent for tests: one session, no network, no credential.

Modes (argv):
  (none)      answers session/new with a session id, streams one text delta, and records
              one tool_call update — the shape of a leg that really took our tools.
  --no-tools  accepts the session but ignores `mcpServers` and reports no tool calls:
              what a CLI that does not implement the handoff looks like.
  --permission  asks the client for permission (our MCP tool option + a foreign one)
              before answering, and echoes which option was chosen.
  --silent    never answers session/prompt (the timeout path).
  --banner    prints non-JSON lines before every message (a CLI that greets you).
"""

import json
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else ""


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def update(kind, **extra):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": kind, **extra}}})


def main():
    pending_permission = None
    prompt_id = None
    if MODE == "--banner":
        sys.stdout.write("welcome to fake acp" + chr(10))
        sys.stdout.write("(this line is not JSON)" + chr(10))
        sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method = msg.get("method")
        mid = msg.get("id")

        if method is None:
            # A response to something we asked (the permission request).
            if pending_permission is not None and mid == pending_permission:
                outcome = (msg.get("result") or {}).get("outcome") or {}
                update("agent_message_chunk", content={"type": "text", "text": f"chosen={outcome.get('optionId', 'none')}"})
                # Answer the PROMPT's id, not the permission request's id: that was
                # the first version of this fake and it hung the client forever.
                send({"jsonrpc": "2.0", "id": prompt_id, "result": {"stopReason": "end_turn"}})
                pending_permission = None
                prompt_id = None
            continue

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "session/new":
            params = msg.get("params") or {}
            servers = params.get("mcpServers") or []
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "fake-session"}})
            if MODE != "--no-tools" and servers:
                update("tool_call", toolCallId="t1", title="plobi: read_file", status="pending")
                update("tool_call_update", toolCallId="t1", status="completed")
        elif method == "session/prompt":
            if MODE == "--silent":
                continue
            if MODE == "--permission":
                prompt_id = mid
                pending_permission = 900
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": pending_permission,
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": "fake-session",
                            "options": [
                                {"optionId": "allow_plobi_read", "kind": "allow_once", "name": "plobi read"},
                                {"optionId": "allow_shell", "kind": "allow_once", "name": "shell"},
                            ],
                        },
                    }
                )
                continue
            update("agent_message_chunk", content={"type": "text", "text": "hello from fake acp"})
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        else:
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unsupported"}})


if __name__ == "__main__":
    main()
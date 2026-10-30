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
  --needs-auth  refuses session/new the way an unsigned CLI does (measured on cline
              3.0.74: `-32000 Authentication required: Call authenticate before
              starting a session`) and names its sign-in methods in `initialize`.
  --no-set-model  does not speak `session/set_model` (-32601), so a leg riding it can
              serve one model only: the one that session is already on.
  --slow-init   drops the first `initialize` without a word and answers later ones —
              the shape of qwen-code 0.26.0, which does not read stdin for ~20 s.
  --needs-auth  refuses session/new the way an unsigned CLI does (measured on cline
              3.0.74: `-32000 Authentication required: Call authenticate before
              starting a session`) and names its sign-in methods in `initialize`.
  --auth-file=PATH  the same refusal, but only while PATH does not exist. Writing the
              file mid-test is "he just signed in", which is the one moment the card is
              supposed to flip by itself — no restart, no second probe mode.
"""

import json
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else ""
AUTH_FILE = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--auth-file=")), "")
_init_seen = 0


def _refuse() -> bool:
    if MODE == "--needs-auth":
        return True
    if MODE.startswith("--auth-file"):
        return not os.path.exists(AUTH_FILE)
    return False

#: The models this stand-in claims to run, in its own ids and names. Answering with them
#: is what lets a test pin "the picker showed what the CLI reported, not a config list".
MODELS = {
    "currentModelId": "m-a",
    "availableModels": [
        {"modelId": "m-a", "name": "Model A"},
        {"modelId": "m-b", "name": "Model B"},
    ],
}

chosen_model = "m-a"


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def update(kind, **extra):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": kind, **extra}}})


def main():
    global chosen_model, _init_seen
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
            _init_seen += 1
            if MODE == "--slow-init" and _init_seen == 1:
                continue
            result = {"protocolVersion": 1, "agentCapabilities": {}}
            if _refuse():
                result["authMethods"] = [{"id": "fake", "name": "Sign in with Fake"}]
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "session/new":
            if _refuse():
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "error": {
                            "code": -32000,
                            "message": "Authentication required: Call authenticate before starting a session",
                        },
                    }
                )
                continue
            params = msg.get("params") or {}
            servers = params.get("mcpServers") or []
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "fake-session", "models": MODELS}})
            if MODE != "--no-tools" and servers:
                update("tool_call", toolCallId="t1", title="plobi: read_file", status="pending")
                update("tool_call_update", toolCallId="t1", status="completed")
        elif method == "session/set_model":
            if MODE == "--no-set-model":
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unsupported"}})
                continue
            chosen_model = str((msg.get("params") or {}).get("modelId") or "")
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
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
                                # The spec's generic three: the option id says nothing
                                # about the tool. Cline, measured 2026-10-30, sends
                                # exactly these ids and names the tool in toolCall.
                                {"optionId": "allow_once", "kind": "allow_once", "name": "Allow once"},
                                {"optionId": "allow_always", "kind": "allow_always", "name": "Allow always"},
                                {"optionId": "reject_once", "kind": "reject_once", "name": "Reject"},
                            ],
                            "toolCall": {
                                "toolCallId": "plobi_read_0",
                                "title": "plobi_read: notes.md",
                                "kind": "other",
                                "status": "pending",
                            },
                        },
                    }
                )
                continue
            update(
                "agent_message_chunk",
                content={"type": "text", "text": f"hello from fake acp on {chosen_model}"},
            )
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        else:
            if mid is not None:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unsupported"}})


if __name__ == "__main__":
    main()
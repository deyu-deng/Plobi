"""A stand-in ACP agent that only publishes a catalog (for acp_model_catalog tests)."""

import json
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else ""

if MODE == "--banner":
    sys.stdout.write("banner line that is not JSON" + chr(10))
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if MODE == "--silent":
        continue
    mid = msg.get("id")
    if msg.get("method") == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1}}) + chr(10))
    elif msg.get("method") == "session/new":
        sys.stdout.write(json.dumps({
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "sessionId": "fake",
                "configOptions": [{
                    "id": "model",
                    "name": "Model",
                    "type": "select",
                    "options": [
                        {"value": "opencode/beam-free"},
                        {"value": "opencode/big-pickle"},
                        {"value": "opencode/glm-5.3-flash-free"},
                    ],
                }],
            },
        }) + chr(10))
    sys.stdout.flush()

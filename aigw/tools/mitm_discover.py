r"""aigw protocol-discovery tool (mitmproxy addon + offline report generator).

Two ways to use this file:

  (A) As a mitmproxy addon (the normal capture path):
        mitmdump -s tools/mitm_discover.py --set app=cursor --set out=captures
      It captures every flow that matches the chosen app's hosts, dumps raw
      request/response bytes (including binary protobuf as .binpb) into
      `out/<host>/`, and on exit writes `out/<app>/report.md` with:
        - the endpoints seen
        - auth / metadata header suggestions (ready-to-paste header_templates)
        - a protobuf / Connect skeleton hint (protoc --decode_raw + .proto stub)
        - a concrete "next manual steps" checklist

  (B) As an offline report generator (no mitmproxy needed):
        python tools/mitm_discover.py --report captures/cursor --app cursor
      Re-runs the analysis over already-captured JSON + .binpb files and rewrites
      the report. Handy when you tweak the analysis without re-capturing.

Why this exists: the adapters contain `# VERIFY` markers because the exact upstream
URLs, auth headers, checksums and request envelopes for Cursor / Antigravity /
Workbuddy are undocumented. This tool turns a live capture into the concrete
config/code you need to fill those markers.

--- HOW MITM WORKS ON ELECTRON APPS (verified) --------------------------------
Electron apps talk HTTPS via Node's http(s) module. To decrypt:
  1. run mitmproxy once so it generates ~/.mitmproxy/mitmproxy-ca-cert.pem
  2. launch the target app with proxy + CA trust env vars:
       HTTPS_PROXY=http://127.0.0.1:8080
       HTTP_PROXY=http://127.0.0.1:8080
       NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem   # critical for Node
  3. use the app normally; requests appear in this addon.
Cursor chat is gRPC/Connect protobuf (aiserver.v1.*). Antigravity routes through
daily-cloudcode-pa.sandbox.googleapis.com (Cloud Code envelope over Gemini).
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import time
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# App profiles: which hosts belong to which app, plus discovery metadata.
# `grpc=True` means the chat endpoint speaks Connect (gRPC-Web framing) and you
# will need to recover a .proto. `hosts` are sub-string matchers (host == h or
# host endswith "." + h).
# ---------------------------------------------------------------------------
APP_PROFILES = {
    "cursor": {
        "label": "Cursor (Electron)",
        "hosts": ["api2.cursor.sh", "cursor.sh", "api.cursor.com"],
        "grpc": True,
        "chat_service": "aiserver.v1.ChatService",
        "proto_file": "aigw/proto/cursor.proto",
    },
    "antigravity": {
        "label": "Antigravity (Electron)",
        "hosts": [
            "daily-cloudcode-pa.sandbox.googleapis.com",
            "oauth2.googleapis.com",
            "generativelanguage.googleapis.com",
        ],
        "grpc": False,
        "chat_service": "Cloud Code (Gemini generateContent)",
        "proto_file": None,
    },
    "workbuddy": {
        "label": "Workbuddy",
        "hosts": [],  # unknown upstream host; pass --set hosts=host1,host2
        "grpc": False,
        "chat_service": "(capture to discover)",
        "proto_file": None,
    },
}

# Headers whose *values* are secrets — mask them in the report.
MASK_VALUE = {"authorization", "cookie", "proxy-authorization", "x-auth-token",
              "x-amz-security-token"}
# Headers that are derived per-request (do NOT hardcode; explain in checklist).
COMPUTED_HEADERS = {"x-cursor-checksum", "x-cursor-signature", "x-request-id",
                    "x-goog-trace-id"}
# Headers that are standard and handled by the adapter code, so we tell the user
# not to template them.
HANDLED_HEADERS = {"authorization", "content-type", "accept", "user-agent",
                   "accept-encoding", "host", "connection"}


def _mask(value: str) -> str:
    v = (value or "").strip()
    if len(v) <= 12:
        return "<redacted>"
    return v[:6] + "…" + v[-4:]


def _match_host(host: str, app: str, extra_hosts: list[str]) -> bool:
    if extra_hosts and any(host == h or host.endswith("." + h) for h in extra_hosts):
        return True
    if app == "all":
        return any(host == h or host.endswith("." + h)
                   for prof in APP_PROFILES.values() for h in prof["hosts"])
    prof = APP_PROFILES.get(app)
    if not prof:
        return False
    return any(host == h or host.endswith("." + h) for h in prof["hosts"])


def _is_grpc(ct: str) -> bool:
    ct = (ct or "").lower()
    return ("connect+proto" in ct) or ("application/grpc" in ct) or ("proto" in ct
                                        and "json" not in ct)


# ---------------------------------------------------------------------------
# Pure analysis (no mitmproxy dependency)
# ---------------------------------------------------------------------------
def analyze_flows(flows: list[dict], app: str = "all") -> dict:
    """Turn a list of captured flow dicts into a structured analysis."""
    endpoints: list[dict] = []
    seen_ep: set[tuple] = set()
    header_obs: dict[str, set] = defaultdict(set)
    grpc = False
    sample_req_bodies: list[dict] = []
    hosts: set[str] = set()

    for f in flows:
        host = f.get("host") or ""
        hosts.add(host)
        method = f.get("method", "")
        path = f.get("path", "")
        req_ct = f.get("request_content_type", "")
        key = (method, host, path, req_ct)
        if key not in seen_ep:
            seen_ep.add(key)
            endpoints.append({"method": method, "host": host, "path": path,
                              "content_type": req_ct})
        for name, val in (f.get("request_headers") or {}).items():
            lname = name.lower()
            if lname in MASK_VALUE:
                header_obs[name].add("<redacted>")
            else:
                header_obs[name].add(val)
        if _is_grpc(req_ct):
            grpc = True
        if len(sample_req_bodies) < 3 and f.get("request_body_repr"):
            sample_req_bodies.append({
                "host": host, "path": path, "content_type": req_ct,
                "body": f["request_body_repr"]})

    # sort endpoints by host then path
    endpoints.sort(key=lambda e: (e["host"], e["method"], e["path"]))
    return {
        "app": app,
        "n_flows": len(flows),
        "hosts": sorted(hosts),
        "grpc": grpc,
        "endpoints": endpoints,
        "header_obs": {k: sorted(v) for k, v in header_obs.items()},
        "sample_req_bodies": sample_req_bodies,
    }


def _suggest_header_templates(analysis: dict) -> tuple[str, str]:
    """Return (yaml_snippet, notes) for header_templates."""
    lines = []
    notes = []
    handled = []
    for name in sorted(analysis["header_obs"]):
        lname = name.lower()
        if lname in HANDLED_HEADERS:
            handled.append(name)
            continue
        vals = analysis["header_obs"][name]
        sample = vals[0]
        if lname in COMPUTED_HEADERS:
            notes.append(
                f"- `{name}` is COMPUTED per request (do not hardcode). Capture "
                f"several values and reproduce the derivation; for Cursor see "
                f"burpheart/cursor-tap, then set `cursor.checksum` (or a template).")
            continue
        if lname in MASK_VALUE:
            continue
        if sample in ("<redacted>",):
            continue
        # static-ish header -> suggest a literal template
        lines.append(f'  - {{ name: "{name}", value: "{sample}" }}')
    if handled:
        notes.insert(0, "Authorization / Content-Type are handled by the adapter "
                       "code (auth.type=bearer / dialect). Do NOT template them.")
    yaml = "header_templates:\n" + ("\n".join(lines) if lines else
                                    "  # (no extra non-standard headers captured yet)")
    return yaml, "\n".join(notes)


def _proto_hint(analysis: dict) -> str:
    app = analysis["app"]
    prof = APP_PROFILES.get(app, {})
    svc = prof.get("chat_service", "the chat service")
    proto_file = prof.get("proto_file") or "aigw/proto/<app>.proto"
    return f"""The `{svc}` endpoint uses **Connect (gRPC-Web framing)**, so the
request/response bodies are protobuf, not JSON. To recover the schema:

```bash
# 1. extract one captured request body to a file (binary .binpb written by this tool)
ls captures/{app}/*.binpb
# 2. decode the unknown schema:
protoc --decode_raw < captures/{app}/<file>.binpb
# 3. copy the fields you see into {proto_file}, then generate stubs:
protoc --python_out=aigw/proto --proto_path=aigw/proto {proto_file}
# 4. implement _build_request / _to_oai / _parse_stream in aigw/providers/cursor_proto.py
```

Minimal `{proto_file}` skeleton to start from:

```protobuf
syntax = "proto3";
package {app.replace('-', '_')};

// VERIFY every field name against `protoc --decode_raw` output.
service ChatService {{
  rpc StreamUnifiedChat (StreamUnifiedChatRequest)
      returns (stream StreamUnifiedChatResponse);
}}

message StreamUnifiedChatRequest {{
  string model = 1;            // VERIFY
  repeated Turn turns = 2;     // VERIFY
  bool stream = 3;             // VERIFY
}}
message Turn {{                        // VERIFY name
  string role = 1;             // "user" | "assistant" | "system"
  string text = 2;
}}
message StreamUnifiedChatResponse {{   // VERIFY
  repeated Part parts = 1;     // VERIFY
}}
message Part {{                         // VERIFY
  string text = 1;
}}
```
"""


def _checklist(analysis: dict) -> str:
    app = analysis["app"]
    if app == "cursor":
        items = [
            "Capture one real chat request: `aigw discover --app cursor --yes`, "
            "then send a message in Cursor.",
            "Decode the protobuf: `protoc --decode_raw < captures/cursor/*.binpb`.",
            "Fill `aigw/proto/cursor.proto` with the real field names/numbers.",
            "Generate stubs: `protoc --python_out=aigw/proto "
            "--proto_path=aigw/proto aigw/proto/cursor.proto`.",
            "Implement the three methods in `aigw/providers/cursor_proto.py` "
            "(_build_request / _to_oai / _parse_stream).",
            "Copy the captured `x-cursor-checksum` value into config "
            "`cursor.checksum` (or reproduce its derivation).",
            "Set `cursor.use_proto: true` and run `aigw start`, then probe "
            "`POST /v1/chat/completions` with model `cursor/auto`.",
            "If you prefer JSON transport, set `cursor.fallback: connect_json` "
            "and provide `cursor.connect_json_body_template` instead of protobuf.",
        ]
    elif app == "antigravity":
        items = [
            "Capture a real request: `aigw discover --app antigravity --yes`, "
            "then prompt Antigravity.",
            "Find the `daily-cloudcode-pa...` POST. Note the exact path "
            "(api_version + model + verb) and the Cloud Code envelope shape.",
            "Set `antigravity.host`, `antigravity.api_version` from the capture.",
            "If the body is wrapped in `{project: ..., request: ...}`, set "
            "`antigravity.cloudcode.envelope: true` + `project` + `request_field`.",
            "Mirror any extra client-metadata headers into "
            "`antigravity.extra_headers` (e.g. x-goog-api-client).",
            "Capture the OAuth `client_id` from the oauth2.googleapis.com call "
            "and set `antigravity.client_id`.",
            "Run `aigw start` and probe `POST /v1/chat/completions` with "
            "model `antigravity/gemini-3-pro`.",
        ]
    elif app == "workbuddy":
        items = [
            "Capture a real request: `aigw discover --app workbuddy --yes "
            "--set hosts=<wb-host>` (Workbuddy's host is not yet known).",
            "Identify the chat path + content-type. Set `workbuddy.base_url` "
            "and `workbuddy.chat_path_template` (may use {model_upstream}).",
            "Pick `workbuddy.dialect`: `openai` if it already speaks "
            "/v1/chat/completions, else `anthropic` (/v1/messages).",
            "Paste the captured auth/metadata headers into "
            "`workbuddy.header_templates` (see the snippet this tool generated).",
            "Map your model: `workbuddy.models` + `workbuddy.model_map`.",
            "Set `workbuddy.accounts[].token` and run `aigw start`; probe "
            "`POST /v1/chat/completions`.",
        ]
    else:
        items = ["Run with a specific --app (cursor|antigravity|workbuddy) for "
                 "a tailored checklist."]
    return "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))


def build_report_md(analysis: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    app = analysis["app"]
    prof = APP_PROFILES.get(app, {})
    hdr_yaml, hdr_notes = _suggest_header_templates(analysis)
    sections = [
        f"# aigw protocol-discovery report — `{app}`",
        f"\nGenerated: {ts}  |  flows captured: {analysis['n_flows']}  |  "
        f"hosts: {', '.join(analysis['hosts']) or '(none)'}",
        "\n## 1. Endpoints observed",
    ]
    if analysis["endpoints"]:
        rows = ["| method | host | path | content-type |",
                "|--------|------|------|--------------|"]
        for e in analysis["endpoints"]:
            rows.append(f"| {e['method']} | {e['host']} | {e['path']} | "
                        f"{e['content_type'] or '-'} |")
        sections.append("\n" + "\n".join(rows))
    else:
        sections.append("\n_(no flows captured for this app yet)_")

    sections.append("\n## 2. Auth / metadata headers (→ header_templates)")
    sections.append("\n```yaml\n" + hdr_yaml + "\n```")
    if hdr_notes:
        sections.append("\n" + hdr_notes)

    if analysis["grpc"]:
        sections.append("\n## 3. Protobuf / Connect hint")
        sections.append("\n" + _proto_hint(analysis))
    elif app in ("antigravity", "workbuddy"):
        sections.append("\n## 3. Request envelope hint")
        sections.append(
            "\nThis app's chat is JSON (not protobuf). Open the captured "
            "`request_body_repr` in captures/<host>/*.json and map the fields "
            "into the adapter (Antigravity: cloudcode envelope; Workbuddy: "
            "dialect + header_templates).")

    sections.append("\n## 4. Next manual steps (checklist)")
    sections.append("\n" + _checklist(analysis))
    return "\n".join(sections) + "\n"


def load_captured_jsons(directory: str | Path) -> list[dict]:
    """Read every *.json flow file under `directory` into flow dicts."""
    directory = Path(directory)
    flows = []
    for p in sorted(directory.rglob("*.json")):
        try:
            flows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return flows


def _flow_from_mitm(flow) -> dict:
    """Convert a mitmproxy HTTPFlow into our flow dict (pure, passes `flow` in)."""
    req = flow.request
    host = req.pretty_host
    req_ct = req.headers.get("content-type", "")
    res_ct = flow.response.headers.get("content-type", "") if flow.response else ""
    return {
        "ts": time.time(),
        "host": host,
        "method": req.method,
        "path": req.path,
        "url": req.pretty_url,
        "request_content_type": req_ct,
        "request_headers": dict(req.headers),
        "request_body_repr": _body_repr(req.raw_content or b"", req_ct),
        "status": flow.response.status_code if flow.response else None,
        "response_content_type": res_ct,
        "response_headers": dict(flow.response.headers) if flow.response else {},
        "response_body_repr": _body_repr(flow.response.raw_content or b"", res_ct)
        if flow.response else None,
    }


def _body_repr(raw: bytes, ctype: str) -> dict:
    if not raw:
        return {"empty": True}
    if "json" in ctype or "text" in ctype:
        try:
            return {"json": json.loads(raw)}
        except Exception:
            return {"text": raw[:2000].decode("utf-8", "replace")}
    # binary protobuf / connect / grpc -> keep bytes for offline decode
    return {"binary_b64": base64.b64encode(raw).decode(),
            "hint": "decode with: protoc --decode_raw < file  (Connect/gRPC framing)"}


def write_report(out_dir: str | Path, app: str, flows: list[dict]) -> Path:
    """Analyze `flows` and write report.md (+ keep per-flow JSON). Returns path."""
    out_dir = Path(out_dir)
    analysis = analyze_flows(flows, app)
    rep = build_report_md(analysis)
    report_path = out_dir / "report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(rep, encoding="utf-8")
    # also drop a machine-readable summary
    (out_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


# ---------------------------------------------------------------------------
# mitmproxy addon (only used when run via mitmdump; import is guarded)
# ---------------------------------------------------------------------------
try:
    from mitmproxy import ctx, http  # type: ignore

    class Discovery:
        def __init__(self):
            self.app = "all"
            self.out = "captures"
            self.hosts_extra: list[str] = []
            self.flows: list[dict] = []

        @property
        def _label(self) -> str:
            return f"[aigw-discover:{self.app}]"

        def configure(self, updated):
            self.app = getattr(ctx.options, "app", "all") or "all"
            self.out = getattr(ctx.options, "out", "captures") or "captures"
            extra = getattr(ctx.options, "hosts", "") or ""
            self.hosts_extra = [h.strip() for h in extra.split(",") if h.strip()]

        def response(self, flow: http.HTTPFlow):
            host = flow.request.pretty_host
            if not _match_host(host, self.app, self.hosts_extra):
                return
            rec = _flow_from_mitm(flow)
            self.flows.append(rec)

            # persist per-flow artifacts (json + binary bodies as .binpb)
            d = Path(self.out) / host
            d.mkdir(parents=True, exist_ok=True)
            ts = int(rec["ts"] * 1000)
            (d / f"{ts}_{flow.request.method}.json").write_text(
                json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
            for side in ("request", "response"):
                body = rec.get(f"{side}_body_repr") or {}
                if isinstance(body, dict) and "binary_b64" in body:
                    raw = base64.b64decode(body["binary_b64"])
                    (d / f"{ts}_{flow.request.method}_{side}.binpb").write_bytes(raw)
            print(f"{self._label} captured {flow.request.method} "
                  f"{flow.request.pretty_url}")

        def done(self):
            if not self.flows:
                print(f"{self._label} no matching flows captured; "
                      f"nothing to report.")
                return
            p = write_report(Path(self.out) / (self.app if self.app != "all"
                                               else "_all"), self.app, self.flows)
            print(f"{self._label} wrote report -> {p}")
            print(f"{self._label} open it and follow section 4 (checklist).")

    addons = [Discovery()]
except ImportError:  # mitmproxy not installed (e.g. our test venv) -> no addon
    Discovery = None
    addons = []


# ---------------------------------------------------------------------------
# Offline CLI:  python tools/mitm_discover.py --report <dir> [--app cursor]
# ---------------------------------------------------------------------------
def _cli_report(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Re-generate aigw discovery report from captured files "
                    "(no mitmproxy needed).")
    ap.add_argument("--report", required=True,
                    help="captures directory containing *.json flow files")
    ap.add_argument("--app", choices=list(APP_PROFILES) + ["all"], default="all")
    args = ap.parse_args(argv)
    flows = load_captured_jsons(args.report)
    if not flows:
        print(f"[aigw-discover] no *.json captures found under {args.report}")
        return 1
    p = write_report(Path(args.report), args.app, flows)
    print(f"[aigw-discover] wrote report -> {p}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli_report(sys.argv[1:]))

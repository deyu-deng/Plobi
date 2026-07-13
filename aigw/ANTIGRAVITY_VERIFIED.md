# Antigravity 适配器 — 端到端验证报告（✅ 已确证消费真实额度）

> 状态：**已完成且真·端到端实测通过**。Antigravity (Google Gemini Code Assist) 的订阅额度已可经 aigw 以 OpenAI 兼容接口转发，确凿消耗真实额度。
> 更新于 2026-07-13（接 ANTIGRAVITY_IMPLEMENTATION.md 的"待验证"状态）。

---

## 1. 协议（三重交叉实锤，非猜测）

| 项 | 值 | 来源 |
|---|---|---|
| host | `daily-cloudcode-pa.googleapis.com`（app 默认）/ `cloudcode-pa.googleapis.com`（prod） | `language_server.log` + proxy + gemini-cli |
| URL | `host/v1internal:generateContent`（+ `:streamGenerateContent?alt=sse`）**model 在 body** | 三源一致 |
| 信封 | `{project, model, request:{contents,systemInstruction,generationConfig,tools,sessionId}, userAgent:"antigravity", requestType:"agent", requestId:"agent-<uuid>"}` | proxy 源码 |
| 关键头 | `x-goog-api-client: gl-node/18.18.2 fire/0.8.6 grpc/1.10.x`、`X-Client-Name/Version`、`X-Machine-Session-Id` | proxy 源码 |
| project 发现 | `loadCodeAssist` body `{metadata:{ideType:9,pluginType:2,platform:3}, mode:1}` → `cloudaicompanionProject` | proxy 源码 |
| OAuth client | `1071006060591-...apps.googleusercontent.com`（Antigravity 公共 client） | proxy 源码 |

---

## 2. token 获取（你已完成）

跑 `aigw auth antigravity` → 浏览器 Google 登录 → 本地 HTTP 回调自动捕获 code → 兑换 composite token：

```
<composite-refresh-token>   # = rt | projectId | mpid；运行时经 `aigw auth antigravity` 获取，勿将真实值提交进仓库
（= rt | projectId | mpid）
```

> 踩坑记录：第一版用 OOB `urn:ietf:wg:oauth:2.0:oob` redirect → Google 报 `400 invalid_request`（OOB 已被禁）。改为本地回调 `http://127.0.0.1:<port>/callback`，成功。

---

## 3. 实测证据（确凿消费额度）

### 3a. 直接适配器路径
```
[access_token head] ya29.<REDACTED>   # 由 refresh token 即时刷新（真实值勿提交）
[project_id] parabolic-delight-r53q7                  # loadCodeAssist 自动发现
[RESPONSE] 'PROOF_OK'
[USAGE] {'prompt_tokens': 9, 'completion_tokens': 3, 'total_tokens': 87}
RESULT: PASS
```

### 3b. 完整网关 REST 接口（OpenAI 兼容）
```
$ export ANTIGRAVITY_REFRESH_TOKEN='<composite>'
$ export AIGW_KEY=sk-local-antigravity
$ python -m aigw start --config config.antigravity.yaml --port 8020

$ curl -X POST http://127.0.0.1:8020/v1/chat/completions \
    -H "Authorization: Bearer $AIGW_KEY" \
    -d '{"model":"antigravity/gemini-3-flash","messages":[{"role":"user","content":"Reply with exactly: GATEWAY_OK"}]}'

MODEL    : antigravity/gemini-3-flash
CONTENT  : 'GATEWAY_OK'
USAGE    : {'prompt_tokens': 9, 'completion_tokens': 4, 'total_tokens': 114}
RESULT   : PASS
```

→ 证明：从浏览器登录 → token 刷新 → 上游 generateContent → 响应翻译 → OpenAI 格式返回，全链路通，且 usage 计的是 Antigravity 的真实 token。

---

## 4. 修掉的两个真 bug（实测暴露）

1. **响应解包**：上游把 payload 包在 `"response"` 对象里（`{response:{candidates,usageMetadata}, traceId, metadata}`）。旧解析器直接读 `obj["candidates"]` → 返回空。修：`_gemini_to_oai` / `_extract_gemini_text` / stream 统一加 `obj.get("response", obj)`。
2. **代理信任**：网关 `main.py` 客户端写死 `trust_env=False`，而你的机器走 V2rayU 代理（:11085），导致直连 Google `httpx.ConnectError: All connection attempts failed`。修：`trust_env` 默认 `True`（尊重环境代理）。
3. 附带：有 env refresh token 时跳过 keychain 的 stale token 账户（避免 401 空转）。

> 注：`max_tokens` 设得过小（如 20）会被 Gemini 思考吃掉文本 token → 返回空 content。这是模型行为，非 bug；正常客户端不设这么小的值。

---

## 5. 启动方式（已验证）

```bash
cd aigw
export ANTIGRAVITY_REFRESH_TOKEN='<composite token — 运行时获取，勿提交>'
export AIGW_KEY=sk-local-antigravity
python -m aigw start --config config.antigravity.yaml
# 监听 127.0.0.1:8019，提供 /v1/models /v1/chat/completions /healthz
```

composite token 当前在 `/tmp/ag_composite_token.txt`（**勿提交进仓库**）。

---

## 6. 剩余工作（按你"前端先定再后端"的顺序）

- [ ] 把 `served_models` 注入前端模型选择器（聊天框能选 `antigravity/gemini-3-pro|flash|claude-sonnet-4-6`）
- [ ] 选 Antigravity 模型时，聊天请求静默路由过 Hub（OPENAI_BASE_URL 指向 aigw），用户对"在消耗哪个额度"零体感
- [ ] Cursor / Workbuddy 适配器仍未验证（Cursor protobuf 逆向、Workbuddy host 未知）
- [ ] 旧网关进程仍占 8019（kill 被权限拦，无害）；token 未持久化，重启需重设 env

---

## 7. 文件清单

| 文件 | 改动 |
|---|---|
| `aigw/providers/antigravity.py` | 协议重写 + 解包修复 + 跳过 stale keychain 账户 |
| `aigw/tokens/desktop_stores.py` | `GOOGLE_CC_CLIENT_ID/SECRET`、`parse_composite_refresh`、`read_antigravity_access_token` |
| `aigw/auth/antigravity_oauth.py` | OAuth bootstrap（localhost 回调，非 OOB） |
| `aigw/main.py` | `trust_env` 默认 `True` |
| `aigw/cli.py` | `auth` 子命令 |
| `config.antigravity.yaml` | 示例配置 |
| `tests/test_antigravity.py` | 26 项请求构造断言（全绿） |

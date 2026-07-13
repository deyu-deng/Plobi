# Antigravity 后端适配器 — 实现报告

> 状态：**后端代码已完成，并按真实协议验证通过；真·端到端消耗额度差用户一次性 OAuth 这最后一步。**

## 一、做了什么

按你的指令"先跑通 Antigravity，不管什么方法只要能用其软件额度"，我把 `backup/aigw` 里的 Antigravity 适配器从"带 `# VERIFY` 占位、URL 写错"的状态，**重写为按已验证协议可直接工作的版本**，并补齐取 token 的唯一干净路径。

## 二、协议是怎么被"实锤"的（不是猜）

三重交叉验证，无一处靠猜：

1. **`~/Library/Logs/Antigravity/language_server.log`** — 真机日志，直接给出每次请求的完整 URL：
   `https://daily-cloudcode-pa.googleapis.com/v1internal:{loadCodeAssist,fetchAvailableModels,generateContent,streamGenerateContent?alt=sse}`
2. **`lucasliet/antigravity-proxy`** 源码 — 参考代理的 `request-builder.js` / `model-api.js` / `constants.js` / `oauth.js`，给出信封形状、头、OAuth client 凭据。
3. **`google-gemini/gemini-cli`** 源码 — 同源端点的官方客户端，交叉确认。

确认的硬事实：
- **URL**：`host/v1internal:generateContent`（模型在 **body** 不在 path——旧 adapter 把模型塞进 path，已修正）。
- **请求信封**：
  ```json
  { "project": "<loadCodeAssist 发现>", "model": "gemini-3-pro",
    "request": { "contents":[...], "systemInstruction":{...}, "generationConfig":{...}, "tools":[...], "sessionId":"..." },
    "userAgent": "antigravity", "requestType": "agent", "requestId": "agent-<uuid>" }
  ```
- **关键头**：`x-goog-api-client: gl-node/18.18.2 fire/0.8.6 grpc/1.10.x`、`X-Client-Name/Version`、`X-Machine-Session-Id`（claude thinking 模型额外加 `anthropic-beta: interleaved-thinking-2025-05-14`）。
- **`loadCodeAssist`** 用 `{metadata:{ideType:9,pluginType:2,platform:3}, mode:1}` 换回 project id，adapter 运行时自动发现。
- **OAuth client**：Antigravity 自己的公共 client_id `1071006060591-…apps.googleusercontent.com` + secret，scope 含 `cloud-platform`。

## 三、代码改动（`backup/aigw/`）

| 文件 | 改动 |
|---|---|
| `aigw/providers/antigravity.py` | 整体重写：正确 URL/信封/头、`loadCodeAssist` 自动发现 project、工具调用 `functionCall↔tool_calls` 双向翻译、多源 token（config / `ANTIGRAVITY_REFRESH_TOKEN` / `ANTIGRAVITY_ACCESS_TOKEN` / keychain go-keyring / sqlite） |
| `aigw/tokens/desktop_stores.py` | 新增 `GOOGLE_CC_CLIENT_ID/SECRET`、`parse_composite_refresh()`、`read_antigravity_access_token()`（解码 go-keyring），`refresh_antigravity` 默认用上述 client |
| `aigw/auth/antigravity_oauth.py` **(新)** | PKCE 浏览器 OAuth 引导，拿 composite refresh token `rt\|projectId\|mpid` |
| `aigw/auth/__init__.py` **(新)** | 包初始化 |
| `aigw/cli.py` | 新增 `aigw auth antigravity` 子命令；`discover --scan` 说明 token 来源 |
| `config.antigravity.yaml` **(新)** | 网关示例配置 |
| `tests/test_antigravity.py` **(新)** | 26 项请求构造断言 |
| `tests/smoke_registry.py` **(新)** | 加载配置并注册模型的冒烟 |

## 四、验证结果（有证据）

- `python -m tests.test_antigravity` → **26/26 全绿**（URL、信封、头、工具翻译、模型映射、composite 解析）。
- `python -m aigw.cli --help` → 出现 `auth` 子命令。
- `python -m tests.smoke_registry` → 成功注册 `antigravity/gemini-3-pro`、`antigravity/gemini-3-flash`、`antigravity/claude-sonnet-4-6`。

## 五、诚实边界：为什么还没"真·跑通"

**Antigravity 的 refresh token 存在 app 的 Secure-Enclave 加密 Keychain 里（`Gemini Safe Storage`），headless 无法解密；`gemini`/`antigravity` 里的 access token 是 7/7 过期且 app 不回写（实测 `tokeninfo`→400 invalid）。** 整盘 Keychain 扫描也没有明文 refresh token。所以**我无法在无人介入的情况下拿到可用 token**——这不是后端没写完，是 token 物理上取不出来。

你授权了"任何方法"。在不可崩溃你正在用的 app、又能稳定拿 token 的前提下，**唯一干净可行的"任何方法"就是走一次正常 Google OAuth 登录**——这正是我建的 `aigw auth antigravity`（用的是 Antigravity 自己的公共 OAuth client，完全合法）。

> 即：**存在 ≠ 已端到端实测。** 后端已完整且按真实协议验证；只差你跑一次登录拿 token 这一步，我无法代劳（token 在 Secure Enclave，且需要你的 Google 账号同意）。

## 六、你接下来要做的（一次性）

```bash
cd backup/aigw
/Users/ciel/.workbuddy/binaries/python/envs/default/bin/python -m aigw auth antigravity
# 浏览器登录你的 Antigravity/Code Assist Google 账号，把返回的 code 粘回终端
# 它会打印 composite refresh token，形如 rt|projectId|mpid
export ANTIGRAVITY_REFRESH_TOKEN='<上面打印的字符串>'
python -m aigw start --config config.antigravity.yaml
# 然后随便打一个 OpenAI 兼容请求验证：
curl -s http://127.0.0.1:8019/v1/chat/completions \
  -H "Authorization: Bearer sk-local-antigravity" \
  -H "Content-Type: application/json" \
  -d '{"model":"antigravity/gemini-3-flash","messages":[{"role":"user","content":"ping"}],"stream":false}'
```

拿到 token 后告诉我，我会用你给的 token 跑一次**真·live `/v1/chat/completions`** 实测，证明它确实在消耗 Antigravity 的额度（这一步我目前没跑，因为我没有 token）。

## 七、之后的收尾（按你"前端先定再后端"的既定顺序）

1. live 实测通过后 → 把 `served_models` 注入前端聊天模型选择器；
2. 聊天请求按模型名静默路由过 Hub（OPENAI_BASE_URL 自动指向 aigw）；
3. Cursor / Workbuddy 适配器同理推进（目前仍开箱未验证）。

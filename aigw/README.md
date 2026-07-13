# aigw — 本地统一 OpenAI 兼容额度聚合网关

把 **Cursor / Antigravity / Workbuddy 等桌面端应用**的额度，聚合到一个本地
OpenAI 兼容接口（`/v1/chat/completions`），供你自己的 AI 软件调用。

架构借鉴 [Sub2API](https://github.com/Wei-Shaw/sub2api)（账号池 / 智能调度 / 粘性会话 /
熔断计费）与 [antigravity-proxy](https://github.com/lucasliet/antigravity-proxy)（格式翻译 /
Cloud Code 包装 / 429 退避轮询），并用 mitmproxy 做协议逆向发现。

> ⚠️ **合规与风险（务必先读）**
> 复用桌面客户端 OAuth token 去驱动第三方软件，**违反 Cursor / Google(Antigravity) 等厂商 ToS**，
> 可能导致封号。lucasliet/antigravity-proxy 的 README 顶部已挂"Google 正在因此封号"的警告。
> 本项目仅用于**个人本地开发/研究**。能否长期稳定使用取决于厂商风控，**不作任何"可用"保证**。
> 详见末尾「风险与最佳实践」。

---

## 已验证 vs. 待打通（诚实清单）

| 部分 | 状态 | 说明 |
|---|---|---|
| 网关骨架（FastAPI/OpenAI 接口/鉴权/健康检查） | ✅ 已跑通 | TestClient 验证：401 拦截、`/v1/models`、`/healthz` |
| 调度器（粘性会话 / 失败转移 / 跨 provider / 429 熔断退避 / 流式并发槽） | ✅ 已跑通 | 单元测试验证 sticky + failover + 跨 provider + stream |
| Token Manager（加密保险库 + 后台刷新 + 每 app 健康/last_used） | ✅ 已跑通 | Vault 加解密 round-trip + 后台刷新单测通过 |
| Cursor token 读取（`state.vscdb`）与刷新端点 | ✅ 路径/密钥已核实 | 本机实测**读到了真实 token**；刷新走 `api2.cursor.sh/oauth/token` |
| Antigravity token 读取（`~/.antigravity/db.sqlite`）| ✅ 路径已核实 | 本机未装该应用；读取逻辑已就绪 |
| **Cursor 对话协议** | ⚠️ 需逆向 | 是 gRPC/Connect **protobuf**（`aiserver.v1.ChatService`）+ `x-cursor-checksum` 风控头，**非 REST**。用 `aigw discover` 抓 `.proto` 后生成 `cursor_pb2` 再实现 |
| **Antigravity Cloud Code 请求封套** | ⚠️ 需核实 | Gemini 翻译已写好；Cloud Code 的 URL 前缀/project 封套已做成 config 驱动（`cloudcode.*`），需 mitm 确认真实形状 |
| **Workbuddy 内部 API** | ⚠️ 无公开资料 | config 驱动的通用透传 + header 模板 + anthropic dialect；端点/鉴权抓包后填 `config.yaml` |
| **Mock provider（本地回显/静态/故障注入）** | ✅ 已跑通 | 零网络、零厂商账号；全栈 e2e 测试 8 项通过（鉴权/模型/健康/流式 SSE/404/跨账号失败转移/reauth 熔断） |

代码里所有需要抓包确认的点都标了 `# VERIFY`。**"存在≠能用"**——上游调用要跑通，必须先用下面的
mitm 工具把这些点填实。

---

## 快速开始（mock，零风险本地起网关）

不想碰任何厂商 ToS、只想先把网关跑起来验证整条管线？`mock` provider 用本地构造的
OpenAI 格式响应，不联网、不读任何桌面端 token。

### 1) 写一份最小 config

`config.example.yaml` 已含 mock 块（默认启用）。最小可用配置（仅 mock、不开 vault）：

```yaml
server:
  host: 127.0.0.1          # 保持本地；不要绑 0.0.0.0 除非前面有鉴权
  port: 8000
  api_key: sk-local-dev-key   # 你自己的软件调用网关时带的 key
logging:
  level: INFO
providers:
  mock:
    enabled: true
    mode: static
    reply: "mock reply"
    embedding_dim: 8
    models: ["mock/echo", "mock/static", "mock/embedding"]
    accounts:
      - { id: mock-ok, mode: static }
      - { id: mock-echo, mode: echo }
```

### 2) 起服务

```bash
cd aigw
cp config.example.yaml config.yaml          # 或粘上面的最小配置
python -m aigw start                        # 默认 127.0.0.1:8000
# 自定义：python -m aigw start --config config.yaml --host 127.0.0.1 --port 8000
```

`aigw start` 用 uvicorn 拉起 FastAPI app，`--host/--port` 优先于 `server` 段；日志默认
INFO 打到控制台（可在 `logging.file` 指定文件落盘）。

### 3) 验证（curl）

```bash
B=http://127.0.0.1:8000
K="Authorization: Bearer sk-local-dev-key"

curl $B/v1/healthz -H "$K"                                   # 账号池状态
curl $B/v1/models  -H "$K"                                   # 模型清单（完整 OpenAI 对象）
curl $B/v1/chat/completions -H "$K" -H "Content-Type: application/json" \
  -d '{"model":"mock/echo","messages":[{"role":"user","content":"hello"}]}'   # 回显
curl $B/v1/chat/completions -H "$K" -H "Content-Type: application/json" \
  -d '{"model":"mock/static","stream":true,"messages":[{"role":"user","content":"hi"}]}'  # 流式 SSE
curl $B/v1/embeddings -H "$K" -H "Content-Type: application/json" \
  -d '{"model":"mock/embedding","input":"hello world"}'       # 向量（mock 确定性伪向量）
```

### 4) 验证（OpenAI Python SDK）

```python
from openai import OpenAI
c = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="sk-local-dev-key")

# 非流式
print(c.chat.completions.create(
    model="mock/echo",
    messages=[{"role":"user","content":"ping pong"}]).choices[0].message.content)

# 流式
for chunk in c.chat.completions.create(
        model="mock/static", stream=True,
        messages=[{"role":"user","content":"hi"}]):
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")

# embeddings
print(c.embeddings.create(model="mock/embedding", input="hello world").data[0].embedding)
```

### 5) 跑测试

```bash
python -m pytest aigw/tests/test_gateway_e2e.py -v   # 9 项：鉴权/模型/健康/流式/404/失败转移/reauth/embeddings
```

`mock` 的账号支持 `mode: echo | static | fail | dead`，可分别复现"正常回复 / 固定回复 /
限流 429 触发调度器冷却+跨账号重试 / 死 token 触发 reauth 熔断→503"，方便在接入真实
provider 之前把调度与熔断逻辑压实。

---

## 从 mock 到真实上游（迁移 checklist）

mock 只证明管线和调度可用。**真实额度聚合必须抓包填实协议**，否则上游调用会 501/失败。
一步步来：

- [ ] **1. 选目标 app**：Cursor / Antigravity / Workbuddy 三选一，逐个打通，别一次全开。
- [ ] **2. 只读扫描**：`aigw discover --scan` 确认本机已登录会话能被读到
      （Cursor `state.vscdb`、Antigravity `db.sqlite`）。读不到就先登录对应客户端。
- [ ] **3. 抓真实流量**：`aigw discover --app cursor --yes`（默认 dry-run，需 `--yes` 才真正
      起 mitmdump）。按提示用 `NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem` 把目标
      app 走代理启动，手动发几条消息。
- [ ] **4. 看报告**：`aigw discover --report captures/<app>` 生成 Markdown，重点记录
      - 真实 chat 端点 host + path（核对代码里标 `# VERIFY` 的默认值）
      - 鉴权/风控头（`x-cursor-checksum`、client-metadata 等）→ 填进 config 的
        `extra_headers` / `header_templates`
      - 请求体结构（Cursor 是 Connect gRPC/protobuf，需抓 `.proto` 后用 `protoc` 生成
        `cursor_pb2`，再置 `cursor.use_proto: true`；或提供 `connect_json_body_template`）
- [ ] **5. 填 config**：把上面三点写进 `config.yaml` 对应 provider 段，关闭 mock 或并存。
- [ ] **6. 小流量试跑**：用上面的 curl/SDK 打真实模型（如 `cursor/gpt-4o`），确认返回 200
      且内容是真实回复；观察 `/healthz` 账号状态与 token 健康。
- [ ] **7. 开启保险库（可选）**：`vault.enabled: true` 加密落盘 token，后台预刷新。
- [ ] **8. 持续合规自查**：厂商风控会变，封号风险自担；本工具仅供个人本地研究，不作
      "长期可用" 保证。抓到的端点/头若失效，回到第 3 步重抓。

> 所有"待核实"的点在代码里标了 `# VERIFY`。**存在 ≠ 能用**：未实跑验证的协议翻译一律
> 视为未完成，宁可 501 报错也不要返回假数据。

---

## 目录结构

```
aigw/
├── aigw/
│   ├── main.py                 # OpenAI 兼容 HTTP 层（/v1/chat/completions, /v1/models, /healthz）
│   ├── cli.py                  # 命令行：aigw start / discover / status
│   ├── __main__.py             # python -m aigw
│   ├── config.py               # YAML + 环境变量加载
│   ├── registry.py             # Provider 注册 + 模型路由（含 routing.rules 跨 provider）
│   ├── scheduler.py            # 账号池调度：粘性/失败转移/跨 provider/熔断/并发槽 + TokenManager 钩子
│   ├── providers/
│   │   ├── base.py             # Provider 抽象契约 + Account/Credential/UpstreamError
│   │   ├── antigravity.py      # OpenAI<->Gemini 翻译 + 可配置 Cloud Code 封套
│   │   ├── cursor.py           # token 已通；对话协议委托 cursor_proto（需 .proto）
│   │   ├── cursor_proto.py     # Connect/protobuf 客户端占位 + protoc 生成说明
│   │   └── workbuddy.py        # config 驱动透传 + header 模板 + openai/anthropic dialect
│   ├── tokens/
│   │   ├── desktop_stores.py   # 读桌面端 SQLite（Cursor/Antigravity）+ 刷新流程
│   │   ├── vault.py            # 加密保险库（keyring / Fernet 文件）
│   │   └── manager.py          # TokenManager：预检查/后台刷新/健康/落盘
│   └── proto/
│       └── cursor.proto        # Cursor 对话 protobuf 骨架（待抓真实字段）
├── tools/
│   └── mitm_discover.py        # mitmproxy addon：抓真实端点/鉴权头/请求体
├── config.example.yaml
├── pyproject.toml
└── requirements.txt
```

## 数据流

```
你的软件 ──OpenAI 请求──► main.py ──resolve(model)──► registry (含 routing.rules)
                                          │
                                          ▼
                                     scheduler.dispatch
                         （选账号→刷新token→熔断/重试/粘性/跨provider）
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    ▼                     ▼                     ▼
             cursor 适配器          antigravity 适配器        workbuddy 适配器
          (Connect/protobuf)    (Gemini/Cloud Code SSE)     (OpenAI/Anthropic 透传)
                    │                     │                     │
              api2.cursor.sh   daily-cloudcode-pa...    你抓包得到的端点
                                          ▲
                              TokenManager 后台刷新 + Vault 加密落盘
```

## 快速开始

```bash
cd aigw
python -m venv .venv && source .venv/bin/activate
pip install -e .                       # 安装依赖 + 注册 `aigw` 命令
# 可选：pip install ".[vault,discover,protobuf]"

cp config.example.yaml config.yaml     # 按需填写（见下方 vault / routing / cloudcode）
export AIGW_KEY=sk-your-local-key

aigw start                             # 等价于 uvicorn aigw.main:app
```

调用（你的软件把 base_url 指到这里即可）：

```bash
curl http://127.0.0.1:8787/v1/chat/completions \
  -H "Authorization: Bearer sk-your-local-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"antigravity/gemini-3-flash","messages":[{"role":"user","content":"hi"}]}'
```

---

## Token Manager（加密存储 + 后台刷新 + 健康）

每个桌面端 token 都是敏感凭证。`aigw/tokens/` 提供两件事：

1. **加密保险库 `vault.py`**：token 落盘前用 Fernet（AES-128-CBC + HMAC）加密。
   - `backend: keyring` → 主密钥存系统钥匙串（macOS Keychain / libsecret / Win 凭据）。
   - `backend: file` → 主密钥存 `~/.aigw/.key`（权限 `0600`）。
   - `backend: auto` → 优先钥匙串，不可用则回退文件。
2. **TokenManager `manager.py`**：
   - **预检查**：token 在到期前 `preempt_sec`（默认 300s）就主动刷新，避免请求中途 401。
   - **后台刷新**：异步任务每 `refresh_interval` 秒扫一遍所有账号，刷新临期 token 并写健康状态。
   - **每 app 状态**：`last_used`（最近调用时间）+ `health`（`ok`/`degraded`/`dead`），
     暴露在 `/healthz` 的 `tokens` 段，可由 `aigw status` 查看。
   - **加密落盘**：`persist: true` 时把刷新后的 creds 写进 Vault，重启不必立刻重新登录
     （前提是上游允许重放该 refresh token）。

配置（`config.yaml` 的 `vault:` 段）见 `config.example.yaml`。

---

## 协议发现完整流程

所有 `# VERIFY` 点最终都要靠抓包填实。完整步骤：

### 0. 准备
```bash
pip install ".[discover]"          # 装 mitmproxy
mitmdump                           # 先跑一次，生成 ~/.mitmproxy/mitmproxy-ca-cert.pem
```

### 1. 只读扫描（安全，先看本机有哪些登录态）
```bash
aigw discover --scan
```
只读取桌面端本地存储，报告检测到的 session / token，**不改任何东西**。

### 2. 启动拦截 + 启动目标应用
Electron 走 Node 的 https，光设 `HTTPS_PROXY` 不够，**必须 `NODE_EXTRA_CA_CERTS`** 让 Node 信任拦截证书：
```bash
aigw discover --app cursor --yes
# 等价手动命令（aigw 会打印出来）：
HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080 \
NODE_EXTRA_CA_CERTS=~/.mitmproxy/mitmproxy-ca-cert.pem \
/Applications/Cursor.app/Contents/MacOS/Cursor
```
默认 `aigw discover` 只**打印**要执行的命令（`--dry-run`），加 `--yes` 才真的拉起 mitmdump——
因为拦截 + 启动第三方应用属于敏感操作，需要你显式确认。

### 3. 拿到真实流量
正常使用应用，`captures/<host>/*.json` 会记录端点、鉴权头、请求体：
- **JSON / REST** 直接可读；
- **Cursor 的 protobuf/Connect** 自动 base64，并提示用 `protoc --decode_raw` 还原。

### 4. Cursor：从抓包生成 `.proto` 并实现
```bash
protoc --decode_raw < captures/api2.cursor.sh/<file>.bin      # 看字段布局
# 把真实 message 写进 aigw/proto/cursor.proto（替换骨架里的 VERIFY 注释）
protoc --python_out=aigw/proto --proto_path=aigw/proto aigw/proto/cursor.proto
# 生成 aigw/proto/cursor_pb2.py 后，在 config.yaml 设 cursor.use_proto: true，
# 并补全 cursor_proto.py 里的 _build_request / _to_oai / _parse_stream
```

### 5. Antigravity：确认 Cloud Code 封套
看 `captures/daily-cloudcode-pa.sandbox.googleapis.com/*.json`：
- URL 前缀 / `api_version` → 填 `antigravity.host` / `antigravity.api_version`；
- 请求体是否被 `{"project":..., "request":...}` 包了一层 → 设 `cloudcode.envelope: true` + `cloudcode.project`；
- 额外鉴权/客户端头 → 填 `antigravity.extra_headers`；
- thinking → 开 `antigravity.thinking: true`。

### 6. Workbuddy：填 config
把 `base_url` / `dialect` / `auth` / `header_templates` 按抓到的真实请求填进
`workbuddy:` 段，开 `enabled: true`。

---

## 路由规则（routing）

- **别名（aliases）**：把一个具体模型改名，如 `gemini: antigravity/gemini-3-pro`。
- **路由规则（routing.rules）**：把一个名字扇出到**有序的多个 provider**（跨 app 失败转移）。
  只有真正服务该模型（解析后的 `model`）的 provider 才会被尝试，所以候选列表里
  要放"都服务同一个模型"的 app/账号（例如多账号、或镜像模型）。

```yaml
routing:
  rules:
    - match: "fast"                       # 调用方请求 model="fast"
      providers: [antigravity, cursor]    # 先 Antigravity，失败转移 Cursor
      model: antigravity/gemini-3-flash   # 实际下发的模型
```

调度器策略 `scheduler.strategy`：`least_fail`（默认，失败最少优先）或 `round_robin`。

---

## 命令行（CLI）

```bash
aigw start [--config path] [--host H] [--port P]   # 启动网关
aigw discover [--scan] [--app cursor|antigravity|workbuddy] [--dry-run|--yes]
                                                   # 协议发现助手
aigw status  [--config path]                        # 查询运行中网关的健康
```

- `discover --scan`：只读，报告本机检测到的登录态。
- `discover`（默认）：**只打印**要执行的 mitmdump 命令与 App 启动片段（dry-run）。
  加 `--yes` 才真正拉起拦截；`--app` 指定要看哪个应用的启动片段。
- `status`：GET 运行中网关的 `/healthz` + `/v1/models`，打印每 app 的 state / 健康 /
  token 剩余有效期 / 最近调用时间。

---

## 关键设计点

- **账号即资源池**：每个 provider 持有多个 `Account`，状态机 `ACTIVE/COOLDOWN/REAUTH/DISABLED`。
- **401→重新登录，403→禁用，429→指数退避冷却**（对齐 Sub2API 的 token 生命周期策略）。
- **粘性会话**：`x-session-id` 或首条消息哈希把多轮对话钉在同一账号，避免工具调用上下文错乱。
- **风险隔离**：默认只监听 `127.0.0.1`，`trust_env: false` 避免上游调用误走系统代理；
  token 落盘走加密 Vault（0600）；敏感操作（拦截/启动 App）默认 dry-run，需显式 `--yes`。
- **诚实边界**：未知/危险细节全部放在 `config.yaml` 与 `# VERIFY`，不硬编码猜测。

---

## 风险与最佳实践

**法律 / 账号风险**（无法替你规避，只能说明）
- 复用桌面端 OAuth token 驱动第三方软件违反相关厂商 ToS，可能导致**封号**（Google 已公开警告）。
- 本项目按"个人本地开发/研究"设计，不对可用性、稳定性、合规性作任何保证。
- Cursor 的对话流量带 `x-cursor-checksum` 等风控头，协议层一旦被厂商改版就可能整体失效。

**运维最佳实践**
1. **绝对只绑本地**：保持 `server.host: 127.0.0.1`。若需跨机，请放反向代理后并强制鉴权，**不要**直接 `0.0.0.0` 暴露。
2. **强 api_key**：`AIGW_KEY` 用高熵随机串，别用默认值。下游只放你自己的软件。
3. **加密 Vault**：开 `vault.enabled: true`。优先 `backend: keyring`，避免在多用户机器上用明文文件密钥。
4. **dry-run 优先**：`discover` / 任何会启动第三方 App 或写凭证的操作，先不加 `--yes` 看清楚要执行什么。
5. **小流量灰度**：先用单个账号、低频调用验证链路，再开账号池；留意 `health` 变 `dead`（`aigw status`）。
6. **监控失效**：定期 `aigw status` 看 `health` 与 `expires_in`；上游改版导致 401/403 时及时停用对应 provider。
7. **隔离账号**：用于聚合的桌面端账号，建议与你的主力生产账号分开，降低"一损俱损"风险。
8. **不要提交凭证**：`config.yaml` 含 token 请用环境变量（`${VAR}`），密钥文件 `~/.aigw/` 勿入版本库。

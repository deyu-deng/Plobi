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

路线图例：**spawn-CLI** = spawn 本地已装 CLI 子进程（合规）；**GUI** = Windows UI
自动化驱动桌面端（合规，灰区）；**reverse** = 抓包/逆向桌面端 token/API（非合规，仅个人研究）。

| 组件 | 路线 | 状态 | 说明 |
|---|---|---|---|
| 网关骨架（FastAPI/OpenAI 接口/鉴权/健康检查） | — | ✅ 已跑通 | TestClient 验证：401 拦截、`/v1/models`、`/healthz` |
| 调度器（粘性会话 / 失败转移 / 跨 provider / 429 熔断退避 / 流式并发槽） | — | ✅ 已跑通 | 单元测试验证 sticky + failover + 跨 provider + stream |
| Token Manager（加密保险库 + 后台刷新 + 每 app 健康/last_used） | — | ✅ 已跑通 | Vault 加解密 round-trip + 后台刷新单测通过 |
| **antigravity_cli**（spawn `agy`） | spawn-CLI | ✅ 骨架+测试通过 | 用假 `agy` 跑通 spawn→抓输出→OpenAI 翻译；本机需装 `agy` 真跑 |
| **workbuddy**（hybrid 优先 CLI 回退 GUI） | spawn-CLI→GUI | ✅ 逻辑+测试通过 | 用假 CLI 验证"优先 CLI、无则回退 GUI"；GUI 部分需 Windows |
| **workbuddy_cli**（spawn `workbuddy` CLI） | spawn-CLI | ✅ 骨架+测试通过 | config 驱动；本机需装对应 CLI |
| **marvis_cli**（任意带 CLI 的 Marvis） | spawn-CLI | ✅ 测试通过 | config 驱动；覆盖 openmarvis/marvisx-cli 等 |
| **workbuddy_gui**（Workbuddy 桌面端） | GUI | 🧪 骨架 | 需 Windows GUI + `aigw workbuddy-dump` 抓选择器 |
| **marvis_gui**（腾讯 Marvis 桌面端） | GUI | 🧪 骨架 | 需 Windows GUI + `aigw marvis-dump` 抓选择器 |
| **Cursor token 读取（`state.vscdb`）+ 刷新** | reverse | ✅ 路径/密钥已核实 | 本机实测**读到了真实 token**；刷新走 `api2.cursor.sh/oauth/token` |
| **Cursor 对话协议** | reverse | ⚠️ 需逆向 | gRPC/Connect **protobuf**（`aiserver.v1.ChatService`）+ `x-cursor-checksum`；抓 `.proto`→`protoc`→`use_proto`。见 `docs/CURSOR_INTEGRATION.md` |
| **Antigravity Cloud Code 请求封套** | reverse | ⚠️ 需核实 | Gemini 翻译已写好；URL 前缀/project 封套 config 驱动（`cloudcode.*`），需 mitm 确认真实形状 |
| **workbuddy_api**（HTTP 透传兜底） | reverse* | ⚠️ 占位 | 仅当你**自带 API key** 调干净的 HTTP API 才合规；默认关闭 |
| **Mock provider（本地回显/静态/故障注入）** | — | ✅ 已跑通 | 零网络、零厂商账号；全栈 e2e 测试通过 |

> `reverse*`：workbuddy_api 本身不偷 token，但逆向内部 API 仍属灰区；合规做法是走
> `workbuddy_cli` / `workbuddy_gui`。
>
> 代码里所有需要抓包确认的点都标了 `# VERIFY`。**"存在≠能用"**——上游调用要跑通，必须先把
> 这些点填实（spawn-CLI / GUI 路线只需本机装好对应程序；reverse 路线需 mitm 抓包）。

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
# 推荐：安全档（仅 mock + 本机 CLI；reverse/GUI 默认关）
python -m aigw start --config profiles/safe.yaml

# 或完整示例配置（研究用，含 reverse 说明；reverse 默认 enabled:false）
cp config.example.yaml config.yaml
python -m aigw start                        # 默认 127.0.0.1:8000
# 自定义：python -m aigw start --config config.yaml --host 127.0.0.1 --port 8000
```

`GET /v1/models` 会附带每个模型的 `provider` 与 `capabilities`
（`stream` / `tools` / `vision` / `embeddings` / `sessionful` / `compliance`），
供 Plobi 桌面与 Plobi 按能力过滤。多轮对话请传 `x-session-id` 做账号粘性。

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
python -m pytest aigw/tests/test_cli_provider.py -v   # 10 项：spawn-CLI / GUI 优雅禁用 / hybrid 优先 CLI
python -m aigw.tests.test_cli_provider               # 同上，无需 pytest
```

`mock` 的账号支持 `mode: echo | static | fail | dead`，可分别复现"正常回复 / 固定回复 /
限流 429 触发调度器冷却+跨账号重试 / 死 token 触发 reauth 熔断→503"，方便在接入真实
provider 之前把调度与熔断逻辑压实。

---

## 从 mock 到真实上游（迁移 checklist）

mock 只证明管线和调度可用。接真实额度有 **三条路线**，优先合规路线：

**路线 A — spawn-CLI（合规，首选）**：目标 app 提供 CLI（如 `agy`、任意 Marvis CLI、
Workbuddy CLI）。
- [ ] **A1. 装好对应 CLI 并登录**（如 `agy` 首次运行做 OAuth）。
- [ ] **A2. 确认非交互输出**：`agy -p "hi"` 是否把回复打到 stdout（见各 `*_cli.py` 的 VERIFY 注释）。
- [ ] **A3. 开 provider**：`antigravity_cli` / `marvis_cli` / `workbuddy_cli`（`enabled: true`，
      填 `binary` / `prompt_flag` / `model_map`）。CLI 不在 PATH 时自动禁用，可放心并存。
- [ ] **A4. 小流量试跑**：`curl` 打 `antigravity_cli/gemini-3-pro`，确认返回 200 且内容真实。

**路线 B — GUI 自动化（合规，灰区）**：目标 app 只有桌面 GUI（腾讯 Marvis、Workbuddy 桌面端）。
- [ ] **B1.** 在 **Windows GUI 机** `pip install -r requirements.gui.txt`，启动并登录 App。
- [ ] **B2.** `aigw marvis-dump` / `aigw workbuddy-dump` 抓控件树，记下 input / output 选择器。
- [ ] **B3.** 填 `marvis_gui` / `workbuddy.gui` 段的选择器；provider 无 uiautomation/无窗口时优雅禁用。
- [ ] **B4.** headless / CI 跑不了；只能在本机有活 GUI 会话时验证。

**路线 C — reverse 抓包（非合规，仅个人研究）**：无 CLI、无 API 又想榨额度时（Cursor、Antigravity
Cloud Code、Workbuddy 内部 API）。
- [ ] **C1. 只读扫描**：`aigw discover --scan` 确认本机已登录会话能被读到。
- [ ] **C2. 抓真实流量**：`aigw discover --app cursor --yes`（需 `--yes` 才真正起 mitmdump）。
- [ ] **C3. 看报告**：`aigw discover --report captures/<app>`，记录真实端点 / 风控头 / 请求体结构
      （Cursor 的 `.proto` 抓法见 `docs/CURSOR_INTEGRATION.md`）。
- [ ] **C4. 填 config**：写进对应 provider 段，关闭 mock 或并存。
- [ ] **C5. 小流量试跑**：确认返回 200 且内容真实；观察 `/healthz` 账号与 token 健康。
- [ ] **C6. 合规自查**：厂商风控会变，封号风险自担；仅供个人本地研究，不作"长期可用"保证。

通用收尾（任一路线的路线 A/B/C 之后）：
- [ ] **开启保险库（可选）**：`vault.enabled: true` 加密落盘 token（仅 reverse 路线需要 token）。
- [ ] **保持本地**：`server.host: 127.0.0.1`，强 `api_key`，别直接 `0.0.0.0` 暴露。

> 所有"待核实"的点在代码里标了 `# VERIFY`。**存在 ≠ 能用**：未实跑验证的协议翻译一律
> 视为未完成，宁可 501 报错也不要返回假数据。

---

## 目录结构

```
aigw/
├── profiles/
│   └── safe.yaml               # 推荐默认：mock + CLI only（reverse OFF）
├── aigw/
│   ├── main.py                 # OpenAI 兼容 HTTP 层（/v1/chat/completions, /v1/models, /healthz）
│   ├── cli.py                  # 命令行：aigw start / discover / status / *-dump
│   ├── __main__.py             # python -m aigw
│   ├── config.py               # YAML + 环境变量加载
│   ├── registry.py             # Provider 注册 + 模型路由（含 routing.rules 跨 provider）
│   ├── scheduler.py            # 账号池调度：粘性/失败转移/跨 provider/熔断/并发槽 + TokenManager 钩子
│   ├── providers/
│   │   ├── base.py             # Provider 抽象契约 + Account/Credential/UpstreamError
│   │   ├── cli.py              # CliProvider 基类：spawn 本地 CLI 子进程（opendesign 式）
│   │   ├── gui.py              # GuiProvider 基类：Windows UI Automation 驱动桌面端
│   │   ├── antigravity.py      # reverse: OpenAI<->Gemini 翻译 + 可配置 Cloud Code 封套
│   │   ├── antigravity_cli.py  # spawn-CLI: 官方 `agy`（吃自己的 Google 额度）
│   │   ├── cursor.py           # reverse: token 已通；对话协议委托 cursor_proto（需 .proto）
│   │   ├── cursor_proto.py     # Connect/protobuf 客户端占位 + protoc 生成说明
│   │   ├── marvis_cli.py       # spawn-CLI: 任意带 CLI 的 Marvis（config 驱动）
│   │   ├── marvis_gui.py       # GUI: 腾讯 Marvis 桌面端（无 CLI/API）
│   │   ├── workbuddy.py        # reverse 兜底: HTTP 透传（workbuddy_api）
│   │   ├── workbuddy_cli.py    # spawn-CLI: 任意 Workbuddy CLI
│   │   ├── workbuddy_gui.py    # GUI: Workbuddy 桌面端
│   │   ├── workbuddy_hybrid.py # 组合: 优先 CLI 回退 GUI（workbuddy）
│   │   └── mock.py             # 本地回显/静态/故障注入（dev/demo/e2e）
│   ├── tokens/
│   │   ├── desktop_stores.py   # 读桌面端 SQLite（Cursor/Antigravity）+ 刷新流程
│   │   ├── vault.py            # 加密保险库（keyring / Fernet 文件）
│   │   └── manager.py          # TokenManager：预检查/后台刷新/健康/落盘
│   └── proto/
│       └── cursor.proto        # Cursor 对话 protobuf 骨架（待抓真实字段）
├── tools/
│   └── mitm_discover.py        # mitmproxy addon：抓真实端点/鉴权头/请求体
├── docs/
│   ├── CURSOR_INTEGRATION.md   # Cursor 抓包 → protobuf → 集成路径
│   └── GUI_AUTOMATION.md       # Marvis/Workbuddy 控件抓取步骤
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
        ┌───────────────────┬─────────────┼─────────────┬───────────────────┐
        ▼                   ▼             ▼             ▼                   ▼
  cursor 适配器      antigravity 适配器  marvis/wb      marvis_gui /         workbuddy
 (Connect/protobuf) (Cloud Code SSE)    _cli(spawn)    workbuddy_gui(UIA)   (hybrid:CLI→GUI)
        │                   │             │             │                   │
  api2.cursor.sh   daily-cloudcode  CLI 子进程      Windows UI          优先 spawn CLI
                        -pa...        (吃自身额度)   Automation         回退 GUI 自动化
                                          ▲                ▲                   ▲
                              TokenManager 后台刷新 + Vault 加密落盘（reverse 路线才需要 token）
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
                                                   # 协议发现助手（reverse 路线）
aigw status  [--config path]                        # 查询运行中网关的健康
aigw marvis-dump [--title "Marvis"] [--depth 8]     # (Windows) 抓 Marvis 控件树
aigw workbuddy-dump [--title "WorkBuddy"] [--depth 8] # (Windows) 抓 Workbuddy 控件树
```

- `discover --scan`：只读，报告本机检测到的登录态。
- `discover`（默认）：**只打印**要执行的 mitmdump 命令与 App 启动片段（dry-run）。
  加 `--yes` 才真正拉起拦截；`--app` 指定要看哪个应用的启动片段。
- `status`：GET 运行中网关的 `/healthz` + `/v1/models`，打印每 app 的 state / 健康 /
  token 剩余有效期 / 最近调用时间。
- `marvis-dump` / `workbuddy-dump`：在 **Windows GUI 机**打印对应 App 的 UI 控件树
  （控件类型 / Name / AutomationId / ClassName），用来填 `marvis_gui` / `workbuddy_gui`
  的 `input` / `output` 选择器。详见 `docs/GUI_AUTOMATION.md`。

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

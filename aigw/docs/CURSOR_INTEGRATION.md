# Cursor 适配器：抓包后的集成路径

> 状态：**占位（skeleton）**。token 读取 / 刷新已验证可用；**对话协议未完成**，
> 因为 Cursor 的 chat 走的是 `aiserver.v1.ChatService` 的 **Connect / gRPC protobuf**
> + `x-cursor-checksum` 风控头，**不是 REST**。下面是把真实协议填实、让 `cursor.py`
> 真正跑通的完整路径。

源文件：
- `aigw/providers/cursor.py` —— 入口，含 token 流程 + Connect-JSON 兜底 + proto 分支
- `aigw/providers/cursor_proto.py` —— Connect/protobuf 客户端（集成点，字段标 `VERIFY`）
- `aigw/proto/cursor.proto` —— protobuf 骨架（待填真实字段）
- `tools/mitm_discover.py` —— mitmproxy addon，抓真实流量
- `aigw/tokens/desktop_stores.py` —— 读 `state.vscdb` 的 refresh token（已验证）

---

## 两条集成路线

| 路线 | 触发条件 | 说明 |
|---|---|---|
| **A. Connect/protobuf** | `cursor.use_proto: true` 且 `cursor_pb2` 存在 | 二进制 Connect 协议（`application/connect+proto`），最高保真，但需先抓 `.proto` 并 `protoc` 生成 stub |
| **B. Connect-JSON 兜底** | `use_proto: false` 且提供 `connect_json_body_template` | 同一 URL 走 `application/json`，用你手填的 body 模板把 OpenAI 请求映射到 Cursor 的 JSON 消息。无需 `.proto`，但 Cursor 消息 schema 未公开，需自己从抓包推断 |

默认 `fallback: auto`：有 stub 走 A，否则若有模板走 B，都没有就 501。

---

## 路线 A：从抓包到 protobuf（推荐，最高保真）

### 1. 抓原始流量
```bash
aigw discover --app cursor --yes
# 按提示用 NODE_EXTRA_CA_CERTS 把 Cursor 走代理启动，发几条消息
# 记录落在 captures/api2.cursor.sh/*.bin（protobuf 已 base64）
```

### 2. 还原字段布局
```bash
protoc --decode_raw < captures/api2.cursor.sh/<file>.bin
# 看 StreamUnifiedChatRequest 的字段号 / 类型 / 嵌套结构
```

### 3. 写 schema
把上一步的真实 message 写进 `aigw/proto/cursor.proto`，替换骨架里的 `VERIFY`
注释。重点确认：
- 请求 message 名（当前假设 `StreamUnifiedChatRequest`）
- `model` / `turns`(repeated) / `turn.role` / `turn.text` / `stream` 字段名与编号
- 响应 message 名（`StreamUnifiedChatResponse`）与 `parts`(repeated).`text`

### 4. 生成 Python stub
```bash
protoc --python_out=aigw/proto --proto_path=aigw/proto aigw/proto/cursor.proto
# 生成 aigw/proto/cursor_pb2.py
```

### 5. 填 `cursor_proto.py` 的 `VERIFY` 点
- `_build_request`：按真实字段名构建 `StreamUnifiedChatRequest`
- `_to_oai` / `_parse_stream`：按真实 `parts[].text` 解析
- `_headers`：实现 `x-cursor-checksum`（机器 id + 时间戳混淆，见 `burpheart/cursor-tap`）
  或先把抓到的常量 checksum 填进 config 的 `checksum:` 做临时验证

### 6. 开协议
```yaml
cursor:
  enabled: true
  use_proto: true
  host: https://api2.cursor.sh
  chat_path: /aiserver.v1.ChatService/StreamUnifiedChat
```

---

## 路线 B：Connect-JSON 兜底（无需 .proto）

适用于只想快速验证、或 protobuf 字段难还原时。

```yaml
cursor:
  enabled: true
  use_proto: false
  fallback: connect_json
  host: https://api2.cursor.sh
  chat_path: /aiserver.v1.ChatService/StreamUnifiedChat
  checksum: "<从抓包里抄的 x-cursor-checksum>"
  connect_json_body_template:
    model: "{model}"
    prompt: "{prompt}"
    system: "{system}"
    history: "{messages}"     # 原始 JSON 数组
```

占位符：`{model}` `{prompt}` `{system}` `{messages}`（见 `cursor.py::_render_connect_json`）。
映射逻辑：值为恰好 `"{key}"` 时注入对应 JSON 值；出现在更长字符串里时做转义注入。

---

## 风控头 / 合规

- `x-cursor-checksum` 是反滥用头，`cursor.py::_checksum` 目前只回退 config 值。
  真实算法需逆向客户端（灰区），且一改版即失效。
- **合规提醒**：复用桌面端 token 驱动 Cursor 违反其 ToS，有封号风险。本适配器仅用于
  个人本地研究；真实额度接入前请自担风险，且仅在本地 `127.0.0.1` 暴露网关。
- 抓包工具 `discover` 默认 **dry-run**，需显式 `--yes` 才真正起 mitmdump。

---

## 验证清单

- [ ] `captures/` 里有真实 `.bin` 流量
- [ ] `protoc --decode_raw` 能看出字段布局
- [ ] `cursor_pb2.py` 生成成功，`stubs_available()` 返回 True
- [ ] `_build_request` / `_to_oai` 的 `VERIFY` 字段已对照真实 schema 修正
- [ ] `curl` 打 `cursor/gpt-4o` 返回 200 且内容为真实回复（先小流量、单账号）

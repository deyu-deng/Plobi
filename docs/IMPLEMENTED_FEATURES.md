# Vaelis 已实现功能清单（当前真实代码，2026-07-13 核对）

> **重要：项目已重构。** 此前基于 `backend/`+`src-tauri/`+`aigw/` 的清单（2026-07-11 写于本文件）**已失效**——那套代码现整体归档在 `backup/`（旧 Vaelis / "Plobi" 形态）。
>
> 当前根目录是 **Hermes Agent（Nous Research）的上游 fork / 超集**，正在做 Hermes→Vaelis 去品牌化（见 `VAELIS_PATCHES.md`）。分支 `vaelis-dev`，基于上游 tag `hermes-upstream` = commit `4281151`。本清单基于**当前根目录真实代码**逐模块核对（3 路并行探查 + 关键入口验证），非只看 README/AGENTS.md。
>
> 标注：✅ 真实实现（含 file:line）／⚠️ 已实现但为实验性 / 用户触发 / 营销夸大／❌ 声称但代码缺失。

---

## 0. 架构总览
- Python agent core（`agent/` ~100+ 模块，核心 `conversation_loop.py` ~305KB）
- Messaging gateway（`gateway/`，含 `platforms/` 内置 + `plugins/platforms/` 插件）
- CLI（`hermes_cli/`，~60 命令组；入口 `cli.py` 748KB）
- 桌面（`apps/desktop/` Electron）、TUI（`ui-tui/` Ink/React）
- 工具集（`tools/` 70+ 模块）、插件（`plugins/` 平台/浏览器/图像/记忆/MCP）
- 调度（`cron/`）、MCP server（`mcp_serve.py`）

---

## 1. 多模型 Provider ✅
真实适配器，按 `agent.api_mode` 路由：
- Anthropic（`anthropic_adapter.py:735/832`）、Bedrock（`bedrock_adapter.py:1017/1059`）、Gemini 原生（`gemini_native_adapter.py:35/422`，含 OpenAI↔Gemini 翻译）、Codex/OpenAI Responses（`codex_responses_adapter.py:313`）、Azure OpenAI（`azure_identity_adapter.py:190`）、Vertex AI（`vertex_adapter.py:202`）、LM Studio（`lmstudio_reasoning.py:24`）、OpenAI/OpenRouter/xAI/DeepSeek/Nous/Qwen/自定义 base_url 路径。
- 证据：`conversation_loop.py:1421/1631/1852/1963`（anthropic/bedrock/chat_completions 分发）。

## 2. 持久记忆 ✅
- 内置文件型：`MEMORY.md`/`USER.md`，`§` 分隔符（`memory_tool.py:59`），重启重载 → **跨会话召回真有效**。
- `MemoryManager`（`memory_manager.py:495` prefetch / `:558` sync）。
- 外部 provider 插件：honcho、mem0、hindsight、**holographic（真实 SQLite+FTS5 混合检索 `plugins/memory/holographic/store.py:48-49`）** 等。
- 会话历史 SQLite+FTS5（`hermes_state.py:22/170`）。

## 3. 学习循环 / 技能 ⚠️（营销夸大）
- `/learn` 触发 Agent **现场**用 `skill_manage` 写 `SKILL.md`（`learn_prompt.py:99/143`）——**用户触发，无自主后台蒸馏引擎**。
- `learning_graph.py:254` 仅**可视化**；`learning_mutations.py:124/157` 仅用户编辑/删除。
- 技能系统本身真实现（`skill_manager_tool.py`、`skills_hub.py` 158KB）。
- ⚠️ README 称 "closed learning loop / autonomous skill creation" 与代码不符。

## 4. 上下文压缩 ✅
真实 LLM 摘要（`auxiliary_client` 真实网络调用），失败回退丢中间轮：`context_compressor.py:589/1958`、`conversation_compression.py:435/741`。

## 5. 浏览器 / Computer Use ✅
- 云端浏览器插件：browser-use、browserbase、firecrawl（真实网络驱动，`plugins/browser/browser_use/provider.py:188/209`）。
- 本地：tools 含 `browser_cdp_tool.py`、`computer_use_tool.py` 等。

## 6. 图像生成 ✅
OpenAI（`gpt-image-2` 文生图+编辑，`plugins/image_gen/openai:164/273/298`）、FAL（18 模型）、krea、openrouter、xai。真实 API。

## 7. MOA / 子代理委派 ✅
- `moa_loop.py:336/362` 真实并行 fan-out（`ThreadPoolExecutor`）+ aggregator。
- `tools/delegate_tool.py`（155KB）真实子代理委派。

## 8. 凭证池 / 用量计费 ✅（计费依赖外部 Nous Portal）
- `credential_pool.py:508` 多源（env/oauth/device_code/各 CLI 等）+ 写回 `auth.json`；`credential_persistence.py:151` 脱敏（仅存 SHA-256 指纹）。
- `credits_tracker.py:390` 解析响应头；`billing_view.py:199` 拉取 Nous 后端，**不可达时优雅降级**。

## 9. LSP / 代码智能 ✅
`agent/lsp/` 完整 LSP 客户端（client/manager/protocol/servers/workspace）。

## 10. 消息平台网关 ✅（全部实现，注意位置）
- **内置** `gateway/platforms/`：微信 `weixin`、QQbot `qqbot`、元宝 `yuanbao`、Signal `signal`、WhatsApp Cloud `whatsapp_cloud`、BlueBubbles、api_server、webhook、MS Graph webhook。
- **插件** `plugins/platforms/`：Telegram(412KB)、Discord(383KB)、Slack(195KB)、Email、Home Assistant、Matrix、Mattermost、IRC、SMS、Teams、Feishu、WeCom、DingTalk、Line、Google Chat、ntfy、Simplex、Photon、Raft 等。
- 全部经 `platform_registry` 自注册；`gateway/run.py:8665/8688` 先查 registry 再回退内置。
- ⚠️ 西方平台在 `plugins/`，不在 `gateway/platforms/`——README 的"Telegram/Discord/Slack"均真实现，只是位置不同。

## 11. Relay 跨平台连接器 ⚠️ EXPERIMENTAL
真实 WebSocket 连接器（`gateway/relay/adapter.py:46`、`ws_transport.py:204`），但包文档标注实验性、API 不稳、需 `GATEWAY_RELAY_URL` 开启（`relay/__init__.py:1-17`）。

## 12. Slash 命令 ✅
`GatewaySlashCommandsMixin`（`slash_commands.py` 223KB）+ CLI 侧 ~50 handler（`cli_commands_mixin.py`），共享后端模块；`slash_access.py` 访问控制。

## 13. DM 配对 / 授权 ✅
分层授权：`pairing.py`（一次性码 + 审批 + 白名单同步）+ `authz_mixin.py:264`（`_is_user_authorized`：per-platform/env + 聊天级 + 全局 + 配对 store 联合），缓解间接提示注入。

## 14. Cron 调度 ✅（自然语言有限）
真实引擎：`cron/scheduler.py`(175KB) + `croniter` + 间隔/时长/ISO/cron 表达式 + 多平台投递（`_deliver_result`）。
⚠️ `parse_schedule`(`jobs.py:476`) 仅接受**结构化简写**（"every 30m"/"2h"/cron 表达式），**无开放英文 NL 解析**。

## 15. CLI 命令树 ✅（~60 命令组）
`main.py:12283` `_BUILTIN_SUBCOMMANDS` 列出 ~60 组；`hermes_cli/subcommands/` 45+ argparse 模块（model/config/gateway/setup/cron/curator/skills/mcp/memory/doctor/update/kanban/claw/blueprint/dashboard 等）。

## 16. Electron 桌面 ✅（构建级验证）
多窗口、vaelis:// deep link、自定义 git 自更新（非 electron-updater）、~100 IPC 通道、内嵌终端(xterm+node-pty)、通知/剪贴板/FS/Git、云端+网关管理、bootstrap 安装流。
- ❌ **无系统托盘**：仅有透明 pet overlay 装饰窗（`main.ts:7148` setAlwaysOnTop），无 `new Tray`。
- ⚠️ 仅**构建/类型检查/单测**验证（dist 产物 + 30+ 主进程测试）；GUI 运行时（headless 无法实跑）。
- ⚠️ 第三方 UI kit `@nous-research/ui` 仍硬编码 "Hermes Agent" 标记（KNOWN GAP #1，运行可见）。

## 17. TUI（Ink/React）✅
真实交互终端 UI：多行编辑、slash 补全、输入/消息历史、overlays(hotkeys/mouse)（`ui-tui/src/entry.tsx`、`textInput.tsx`）。
⚠️ `hermes_cli/curses_ui.py` 仅是清单多选组件，**非聊天 TUI**。

## 18. 工具集 ✅（70+ 真实，无桩）
terminal/file/web/browser/computer-use/memory/skills/delegate/kanban/cron/messaging/媒体生成/TTS/转录/vision/MCP/审批安全/Microsoft Graph 等（`tools/`）。全仓 `NotImplementedError`/`# stub` 搜索仅命中抽象基类预期方法，无占位工具。

## 19. 终端后端 ✅（6 个全实现）
`tools/environments/` 下 Local(`local.py:930`)、Docker(`docker.py:568`)、SSH(`ssh.py:36`)、Singularity/Apptainer(`singularity.py:158`)、Modal(+managed `modal.py:164`)、Daytona(`daytona.py:30`)，均 `BaseEnvironment` 子类真实调用。

## 20. MCP ✅（client + server）
- Server：`mcp_serve.py`(35KB) FastMCP，10 工具（`hermes mcp serve`）。
- Client：`tools/mcp_tool.py`(245KB) 多传输 stdio/sse/streamable-http + OAuth + stdio watchdog。

## 21. aigw（AI 网关）⚠️ 已 deferred
根目录**未含完整 aigw**（旧版在 `backup/aigw`）。`VAELIS_PATCHES.md` 记 "aigw quota seam deferred"。桌面预留 `vaelisGateway` IPC（`main.ts` preload），但后端未接。

## 22. 国际化
`locales/` 16 个 yaml（中/西/乌尔都等）。未专项验证切换器接线（参照旧项目经验，需警惕"字典齐但无切换 UI"）。

---

## 与营销/文档的 GAP（"存在 ≠ 能用"重点）
1. **学习循环**：README 宣称"自主技能创建/闭环学习" → 实际是 `/learn` 用户触发 + 可视化，**无自主后台蒸馏**。
2. **Electron 托盘**：代码**无系统托盘**（pet overlay 替代）。
3. **Relay**：实验性、未稳定、需手动开启。
4. **Cron 自然语言**：仅结构化简写，非开放 NL。
5. **桌面 GUI**：仅构建/类型/单测验证，headless 未实跑；第三方 UI kit 仍显 "Hermes Agent"。
6. **aigw**：根目录未含完整实现（deferred），桌面仅预留 IPC。
7. **计费/用量**：真实客户端逻辑，但依赖外部 Nous Portal，不可达时降级。

## 与上一版清单的关系
- 上一版（2026-07-11）针对 `backend/`+`src-tauri/`+`aigw/` 的 Plobi 形态 → **现已归档至 `backup/`，全部结论失效**。
- 当前根目录为 Hermes fork，功能面远超旧版（多平台网关、多模型、MOA、LSP、6 终端后端、MCP、70+ 工具等）。

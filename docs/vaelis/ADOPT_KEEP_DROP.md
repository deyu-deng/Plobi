# Vaelis × Hermes — 后端架构拆解 (Adopt / Keep / Drop)

> 目标:Vaelis 定位为 **Hermes 的超集** —— 吸收 Hermes 成熟的生产级后端模式,同时保留 Vaelis 独有护城河(aigw 闭源额度聚合、多 Agent 编排、本地 ollama)。
> 来源:`/tmp/hermes-agent`(MIT,© 2025 Nous Research)。整仓仅根目录 `LICENSE`,**无 NOTICE** —— 移植代码时必须保留 MIT 声明与版权归属(在 Vaelis 侧补 `NOTICE` 注明派生自 Hermes, MIT)。

## 关键纠正(避免错误前提)
- `agent/` **不是 118 个 profile 目录**,而是**核心运行时引擎**:112 个 `.py` 模块 + 子目录 `secret_sources/`、`transports/`、`lsp/`、`pet/`。
- Agent 的 "profile / persona" 是**配置 / 提示词驱动**的,并非目录级实体。设计 Vaelis 的 profile 时应作为一等公民自建,不要假设 Hermes 有同构概念。

## 子系统判定表

| # | 子系统 | 用途 | 成熟度信号 | 关键文件 | 判定 | 理由 |
|---|--------|------|-----------|----------|------|------|
| 1 | `tools/` | 工具定义/注册/分发 | 96 工具目录;`registry.py` 801 LOC;312 个测试文件 | `tools/registry.py`(单例 `registry.register(name, toolset, schema, handler, check_fn, override)`)、`tools/close_terminal_tool.py`(范式) | **ADOPT** | 自注册、circular-import-safe、`check_fn` 门控、toolset 归属齐全,可直接移植,与 aigw 工具面天然契合 |
| 2 | `toolsets.py` | 工具聚合成集 | 971 LOC;452 个 gateway 测试覆盖 | `toolsets.py`(`get_toolset`/`resolve_toolset`)、`toolset_distributions.py` | **ADOPT** | "平台 → 工具集 → 工具" 窄腰映射,正是 Vaelis 按 aigw / 本地模型分路由需要的 |
| 3 | `agent/` | 核心引擎(AIAgent / adapters / dispatch) | 112 模块 ~81k LOC;重测试 | `agent/tool_dispatch_helpers.py`(556 LOC)、`run_agent.py:393`(`class AIAgent`) | **ADOPT 选择性** | 移植 dispatch / adapter 脚手架;profile 概念自建 |
| 4 | `skills/` | 技能格式与加载 | 20 类目;13 测试;`agentskills.io` 兼容 | `tools/skills_tool.py`(`skills_list`/`skill_view`)、`skills/index-cache/*.json` | **ADOPT** | 标准可移植 `SKILL.md`(YAML frontmatter + references/templates/scripts),Vaelis 可直接复用 |
| 5 | `gateway/` + `tui_gateway/` | JSON-RPC 传输层 | `gateway/` 44 文件;`tui_gateway/` 13 文件;452+29 测试 | `tui_gateway/transport.py`(Transport Protocol + contextvars 路由)、`tui_gateway/ws.py` | **ADOPT 传输 / DROP 平台适配器** | 移植 `Transport` 抽象(stdio/WS 双态,干净);WhatsApp 等平台适配器超 Vaelis 范围 |
| 6 | `mcp_serve.py` | MCP 服务暴露 | 990 LOC;文档完善 | `mcp_serve.py`(10 工具:`conversations_list`/`messages_send`/…) | **ADOPT** | 即插即用的协议桥;Vaelis 可同样暴露 aigw 会话,保持 MCP 客户端兼容 |
| 7 | `cron/` | 文件调度 | 11 文件;31 测试 | `cron/scheduler.py`(`tick()`)、`cron/jobs.py` | **ADOPT** | 无依赖的文件调度,适配 Vaelis 编排触发器 |
| 8 | `plugins/` | 插件系统 | 22 插件目录;32 测试 | `plugins/__init__.py`、`plugins/model-providers/`、`plugins/observability/` | **ADOPT 模式 / DROP 多数插件** | "目录 + TOML" 约定好;只留映射到 Vaelis 的(如 model-providers) |
| 9 | `acp_adapter/` | ACP 协议 | 13 文件;仅 3 测试 | `acp_adapter/server.py`、`acp_adapter/tools.py` | **ADOPT if 需 ACP 客户端 / else DROP** | 测试少,按需移植 |
| 10 | `run_agent.py` | 编排主循环 | 6,055 LOC;`tests/run_agent/` 大 | `run_agent.py`(`_dispatch_delegate_task` L5696、`tools/async_delegation.py`) | **ADOPT 重度** | Hermes **已含多 Agent 委派**(`delegate_task`);Vaelis "编排差异化" 部分已存在,扩展即可 |

## Vaelis 独有 · KEEP(护城河,Hermes 没有)
- **aigw 闭源软件额度聚合**:Cursor / Antigravity / Workbuddy 当 OpenAI 兼容模型用 —— Vaelis 核心创新。
- **闭源额度自动检测**:`/aigw/detect` 扫描本机已装闭源 AI 应用。
- **本地 ollama 模型路由** + 配置门禁。
- **富预览**:乐谱(AlphaTab)/ 3D(GLTF)/ 视频时间线。
- **新手引导 + 主题切换**。
- **Tauri 桌面壳 + 专注 / 浮窗模式**。

## 执行优先级(Phase 2 后端采纳)
- **P0** `tools/registry` + `toolsets/` —— 工具系统是整个 Agent 的窄腰,先移植,建立 Vaelis 工具注册规范。
- **P1** `skills/` 格式 + `run_agent` 的 `delegate_task` 多 Agent 委派 —— 直接补我们"多 Agent 编排"短板(且 Hermes 已有雏形)。
- **P2** `tui_gateway` Transport(改接 Vaelis SSE 或 aigw)+ `mcp_serve` —— 协议兼容,让前端 / 外部 MCP 客户端能接。
- **P3** `cron/` 调度 + `plugins/` 模式(仅 model-providers)—— 编排触发器与可扩展点。
- **P4** `acp_adapter/` —— 仅当 Vaelis 目标接入 ACP 客户端时。

## 风险 / 注意
- **许可**:保留 `LICENSE` 文本;在 Vaelis 补 `NOTICE` 注明"派生自 Hermes Agent (MIT, © 2025 Nous Research)"。
- **规模**:`run_agent.py` 6,055 LOC,移植要**渐进**,别整坨搬;先抽 dispatch / delegate 子模块。
- **前提纠正**:别照搬"Hermes 有 profile 目录"的错误假设 —— profile 是配置驱动的。
- **前端协议差异**:Hermes 前端走 JSON-RPC `/api/ws`,Vaelis 后端是 SSE + `file_output`;Phase 2 要重写前端传输层接 Vaelis SSE(UI 不动,只换底层)。

---
*生成:基于 `/tmp/hermes-agent` 源码实地核查。后续动作:按 P0→P4 顺序 port,每步配 Vaelis 测试。*

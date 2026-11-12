# plobi-core

> **裁定 217（2026-11-12）**：插件标识符从 M1 早期遗留的 `plobi-north-star` 改成
> `plobi-core`。这里装的是**现行核心能力**，不是废弃模块；`Docs/specs/capabilities.md`
> 仍把它列为自研层真源。本页只给指向当前实现的指针，接口别照抄旧文。

## 这个插件现在实际承载什么

| 能力 | 真源 |
|---|---|
| Agent 之间的两枚原子工具 `say` / `ask`（裁定 183） | `channel.py` — `SAY_SCHEMA` / `ASK_SCHEMA` / `handle_say` / `handle_ask` |
| 每一跳派工授权票、六态回执、Thread 续接（裁定 216） | `channel.py::TICKET_REPORT_SCHEMA` + `plobi/delegation/tickets.py` |
| 问另一个 Agent 并续在**同一屏** | `agent_ask.py::build_child_command()`（`--resume` + `PLOBI_AGENT_ID`） |
| 投递之后替收件人起一轮（裁定 200） | `wake.py` |
| L1 灵魂模板、开口纪律、回合预算 | `l1_soul_template.md`、`l1_budget.py` |
| 两句冻结话术的窄硬路由 | `hard_route.py` |
| 工具注册面 | `__init__.py::register()`、`_CHANNEL_TOOLS`、`_DOMAIN_TOOLS` |
| 插件 dashboard API | `dashboard/plugin_api.py` |

## 改名范围与兼容边界（裁定 217）

**已改**：目录名、`plugin.yaml` 的 `name`、工具集名 `plobi_core`、模块 slug
`plobi_plugins.plobi_core`、`pyproject.toml` / `MANIFEST.in` 的打包数据、
`registry.py` 的物理路径与 boot 回写、`config.yaml` 模板、doctor 常量、
所有硬编码路径的测试与脚本、Docs 里指向现行路径的活文档。

**刻意保留**（改了会断已发布契约，或不属本次范围）：
- HTTP 路由键 `/api/plugins/plobi-north-star/*`：`dashboard/manifest.json:2` 与
  `apps/desktop/src/lib/plobi-api.ts::BASE` 继续用旧键，挂载逻辑在
  `plobi_cli/web_server.py` 按 manifest.name 拼，不动它。
- 存量用户 config 里 `plugins.enabled` / `disabled` 的旧值：
  `plobi_cli/plugins.py::_normalize_plugin_keys()` 折算成新键，**不回写用户配置**。
- `NorthStar` 类、`get_north_star()`、风险等级 `lib/risk.py`、
  `tests/plobi/test_north_star_risk_levels.py`：这些是**概念名**，不是插件键。
- 终态愿景活文档 `Docs/specs/north-star-operating-model.md`：与插件标识符无关。
- `Docs/PROGRESS.md` / `Docs/rulings/` / `Docs/archive/` 历史档案：按守门口径只加注不重写。

## 明确不要照抄的旧文字

下面这些曾写在更早一版 README 里，**全部已判废**；按用户 11-08「把误导人的废弃文字删掉，免得下次又误导别的 Agent」清掉：

- `plobi` 深工具（`area` + `action`）：已按 `WP-TOOLSET-INBOUND-2` 拆成 `task_*` / `edge_*` / `ops_*` / `learn_*`，注册面里不再有它（`tests/plugins/test_plobi_core.py` 钉着这一点）。
- `plobi_agent_ask`：裁定 183 删掉的 7 枚 intent 嘴之一，**已不存在**；问话统一走 `ask`。
- `分身` / 「自己的 `PLOBI_HOME`」 / `profile state.db`：10-22 起取消「一个 Agent 一个 profile 家」。身份键是 `PLOBI_AGENT_ID`，会话是单库 `PLOBI_STATE_HOME/state.db` + `sessions.agent_id`。**别把 `-p <profile>` 写回来**。
- `check_fn` 那句「没有 live project 分身就隐藏 schema」：现在是 `check_channel_available`（名册里有没有可发的人），跟分身无关。
- `docs/plobi/north_star/API.md`：改名与搬家前的路径，磁盘上不存在。
- `lib/queue.py` / HID / Pico 那段：脚手架（HID 是 mock、Pico 未刷），**别当已交付能力报数**。

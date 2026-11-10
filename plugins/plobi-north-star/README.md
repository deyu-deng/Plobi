# plobi-north-star

> **目录名是 M1 早期遗留**（R-038 全面改名 Plobi 之后没跟着改），但这里装的是**现行核心能力**，
> 不是废弃模块：`Docs/specs/capabilities.md:186,189` 仍把它列为自研层真源。
> 名字没改（见文末），接口别照抄旧文。本页只给指向当前实现的指针。

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

## 明确不要照抄的旧文字

下面这些曾写在上一版 README 里，**全部已判废**；按用户 11-08「把误导人的废弃文字删掉，免得下次又误导别的 Agent」清掉：

- `plobi` 深工具（`area` + `action`）：已按 `WP-TOOLSET-INBOUND-2` 拆成 `task_*` / `edge_*` / `ops_*` / `learn_*`，注册面里不再有它（`tests/plugins/test_plobi_north_star.py` 钉着这一点）。
- `plobi_agent_ask`：裁定 183 删掉的 7 枚 intent 嘴之一，**已不存在**；问话统一走 `ask`。
- `分身` / 「自己的 `PLOBI_HOME`」 / `profile state.db`：10-22 起取消「一个 Agent 一个 profile 家」。身份键是 `PLOBI_AGENT_ID`，会话是单库 `PLOBI_STATE_HOME/state.db` + `sessions.agent_id`。**别把 `-p <profile>` 写回来**。
- `check_fn` 那句「没有 live project 分身就隐藏 schema」：现在是 `check_channel_available`（名册里有没有可发的人），跟分身无关。
- `docs/plobi/north_star/API.md`：改名与搬家前的路径，磁盘上不存在（`Docs/specs/capabilities.md:189`）。
- `lib/queue.py` / HID / Pico 那段：North Star 脚手架（HID 是 mock、Pico 未刷），**别当已交付能力报数**。

## 为什么目录名还没改

`plobi-north-star` 现在是**插件标识符**，不是产品叫法：`pyproject.toml`、`MANIFEST.in`、`plobi_cli/plugins.py` 的加载器、`plobi/agents/registry.py` 的工具集名、dashboard `manifest.json`、桌面可见文案、HTTP 路由 `/api/plugins/plobi-north-star/*` 以及 40+ 档代码与测试都按它解析。改目录名等于改插件加载键与已发布路由，是独立一刀（需要新名字 + 迁移 + 任务书），不是删个旧 README 能顺带做的事。

产品与概念层面按 R-038 一律叫 **Plobi**；这个目录只是还没换的历史键。

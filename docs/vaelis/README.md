# Vaelis — 设计 / 架构意图存档（salvaged from `backup/`）

本目录收录从旧 Plobi 形态仓库（`backup/`，commit `3bab334`） salvaged 过来的**产品与架构意图文档**。
当前 Vaelis 代码库是 Hermes Agent 的上游 fork（根目录 `vaelis-dev`），这些文档是**决策依据与术语权威**，不是实现事实。

## 为什么要这些文档

`backup/` 是 2.6G 的旧归档，其中 99.9% 是构建产物（`src-tauri/target` 1.7G、`backend/.venv` 551M、`hermes-web/node_modules` 295M 等），已于 2026-07-13 清理。
真正对"升级 Vaelis"有用的资产已 fold 进本仓库：

- **`aigw/`（仓库根）** —— 桌面配额聚合网关的**真实 Python 实现**（FastAPI，OpenAI 兼容）。
  当前 `apps/desktop/electron/main.ts` 只负责进程的 auth/start 调用层；网关本体只在这里。
  Electron `resolveAigwDir()` 在 dev 下指向仓库根 `aigw/`。
- **`docs/vaelis/`** —— 本目录，产品/架构意图。

## 文档索引

| 文件 | 内容 | 注意 |
|---|---|---|
| `ADOPT_KEEP_DROP.md` | **架构取舍决策记录**：Vaelis ⊇ Hermes 策略，哪些子系统 Adopt/Keep/Drop，P0–P4 优先级 | 最有价值的决策依据 |
| `CONTEXT.md` | 产品术语权威定义（协调器/专业 Agent、plan.md、轻量调度、渐进式升维） | 术语别名的"禁止词"表 |
| `BULEPRINT.md` | 功能清单重建版（自标"严重过时，勿作事实"） | 仅术语/产品词汇可参考 |
| `UI_DESIGN_SPEC.md` | M3 / NotebookLM 视觉规范 | 可作设计北极星参考 |
| `AUDIT.md` | 验收审计 + 红线 | 历史 |
| `issues/` | issue 规格 / 技术债 backlog | 历史 |

> 实现状态以根目录 `docs/IMPLEMENTED_FEATURES.md` 为准，不要从这些旧文档反推当前代码。

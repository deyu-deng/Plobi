# Mind 记忆系统原生适配方案（Vaelis / Hermes）

> 状态：方案（已逐文件代码核验，含 file:line 证据）。未实现。
> 原则：遵循 AGENTS.md「核心窄腰、能力在边缘」——新增 `plugins/memory/mind/` 插件，**零核心改动**。
> 合规：写入 Mind 必须不触发其 git pre-commit verifier 的 BLOCKER（见 §3）。

---

## 1. 结论（一句话）

Mind 是**文件系统型（Obsidian markdown）个人知识库**，Vaelis 记忆系统正是 **provider 插件化**结构。
「原生适配」在架构上 = 新增一个 `mind` MemoryProvider 插件（file-backed markdown 读写）+ 一个可选的 `/mind` 协议 skill，**完全不动 agent core**。

---

## 2. 已验证的接入点（证据，非猜测）

| 能力 | 证据 | 说明 |
|---|---|---|
| Provider ABC | `agent/memory_provider.py:43` `class MemoryProvider(ABC)` | 抽象基类，定义全部生命周期 |
| 插件动态发现 | `plugins/memory/__init__.py:146` `discover_memory_providers()`、`:183` `load_memory_provider()` | **扫描目录**实例化，无需改核心 |
| 激活方式 | `plugins/memory/__init__.py:339` `_get_active_memory_provider()` 读 `config.yaml` 的 `memory.provider` | 纯配置激活 |
| 注册表≠加载 | `hermes_cli/memory_providers.py:141` `MEMORY_PROVIDERS` 字典 | 仅 desktop UI 配置表单声明；Mind 本地无 key，**可不碰** |
| 每轮 recall | `agent/memory_manager.py:507` `provider.prefetch(...)`、`:535` `queue_prefetch(...)` | 真 caller ✓ |
| 每轮落盘 | `agent/memory_manager.py:596,603` `provider.sync_turn(...)` | 真 caller ✓ |
| 会话结束 | `agent/memory_manager.py:778` + `run_agent.py:3325,3349` `on_session_end(...)` | 真 caller ✓ |
| 镜像内置记忆 | `agent/memory_manager.py:945-951` `on_memory_write(...)`，由 `:982` `notify_memory_tool_write` 触发；caller：`tool_executor.py:1301`、`agent_runtime_helpers.py:2298` | **Vaelis 写 MEMORY.md/USER.md 时会镜像到 Mind** ✓ |
| 参考模板 | `plugins/memory/holographic/`（`plugin.yaml` + `holographic.py` + `store.py` + `retrieval.py`） | 真实 SQLite+FTS5 实现，照抄接入骨架 |

**关键 hook 均为真实调用，非摆设。**

---

## 3. Mind 的真实边界（来自 `Loom/scripts/verifier.py` + 目录实测）

- **根路径**：`/Users/ciel/Mind`（Mac）；Windows `D:\Mind`；双设备 git 同步。
- **verifier 只阻断两类「声明 vs 物理」不符**（退出码 1）：
  1. `Vault/projects` 顶层目录名 ≠ `AGENTS.md §1` 声明的项目列表（`:75-95`）
  2. `Loom/skills` 技能数 ≠ `AGENTS.md` 声明的技能数（`:98-114`）
- **其余校验仅为 WARN（不阻断提交）**：wiki 子目录缺失、INDEX.md wikilink 死链、空项目目录（幽灵目录）。
- **实测安全写入区（verifier 不拦）**：
  - `Vault/projects/vaelis/` —— **已存在**（Vaelis 专属知识，落这里最合规）
  - `Vault/meta/`、`Vault/notes/`、`Vault/journal/`、`Vault/inbox/`
  - `Loom/wiki/concepts/`、`Loom/wiki/entities/`、`Loom/wiki/sources/`、`Loom/wiki/comparisons/`
  - `Loom/raw/chat-logs/exports/` —— **已存在**（对话日志导出）
  - `Loom/raw/chat-logs/digested/YYYY-MM-DD/`（每日 digest 归档）
- **Mind 强约定**（AGENTS.md）：
  - 文件名 **kebab-case**
  - `Vault/` 禁 AI 元注释
  - 禁 `Vault` → `Loom` 跨区 wikilink
  - 不得**擅自**新建 `Vault/projects/<新项目>` 顶层目录或 `Loom/skills/<新技能>`（会触发 BLOCKER，除非同步改 AGENTS.md 声明——而那不是 Vaelis 该动的）

---

## 4. 设计：`MindProvider(MemoryProvider)`

目录：`plugins/memory/mind/`
```
mind/
  plugin.yaml      # name/version/description/hooks
  __init__.py      # register_memory_provider(MindProvider())
  mind.py          # 核心：file-backed markdown 读写 + 护栏
  retrieval.py     # 关键词/路径检索（可选 sentence-transformers 增强，P2）
```
`plugin.yaml` 示例：
```yaml
name: mind
version: 0.1.0
description: "Native adapter for the Mind second-brain vault (Obsidian markdown). File-backed, zero external deps."
hooks:
  - on_session_end
  - on_memory_write
```

方法映射（对齐 ABC）：
- `name` → `"mind"`
- `is_available()` → 检查 `MIND_ROOT`（env 或 config）存在即可，**无 key 依赖**
- `initialize(session_id, **kwargs)` → 解析 `MIND_ROOT`，建立路径白名单
- `prefetch(query)` → 在 Mind 树做关键词检索（`Loom/wiki/concepts`、`Vault/projects/vaelis`、`Vault/meta`），返回相关 markdown 片段注入系统提示
- `sync_turn(user, asst)` → 每轮后把提炼笔记写到 `Loom/raw/chat-logs/exports/<kebab>.md`
- `on_session_end(messages)` → 端到端 digest 到 `Loom/raw/chat-logs/digested/<DATE>/` 或 `Vault/projects/vaelis/`
- `on_memory_write(action, target, content)` → **镜像 Vaelis 内置记忆**（MEMORY.md/USER.md 写入）到 `Vault/projects/vaelis/memory.md` 或 `Loom/wiki/concepts/`
- `get_tool_schemas()` → 可选暴露 `mind_read` / `mind_write` / `mind_search` 工具给模型（让 Agent 主动读/写 Mind）
- `backup_paths()` → 返回 `MIND_ROOT`，使 `hermes backup` 包含 Mind

**检索实现**：Mind 是结构化 markdown，最稳是 (a) 路径定向读取 + (b) 关键词 grep 全树 `.md`（ripgrep/Python）。向量检索是 P2 增强，非必需（不引入重依赖即满足 P0）。

---

## 5. 合规护栏（写入前必须校验，防止污染知识库）

`mind.py` 写盘前强制：
1. **路径白名单断言**：目标路径必须落在 §3 安全区内，否则拒绝 + `logger.warning`（绝不静默落盘到禁区）
2. **文件名 kebab-case** 归一化
3. **不写 Vault 元注释**、**不写 Vault→Loom 链接**
4. **不动** `AGENTS.md` 的 projects/skills 声明
5. 所有写操作可经 Mind 的 `verifier.py --strict` 回归（见 §7 验收）

---

## 6. 可选增强（分期）

- **P1 — `/mind` 协议 skill**：Vaelis 收到 `/mind` 时加载 `Mind/AGENTS.md`、切到「Main Agent」角色、跑 4 大工作流（参考 Mind 自带 `Loom/skills/mind/`）。这是「原生适配」体验闭环。
- **P1 — 模型工具暴露**：`get_tool_schemas` 暴露 `mind_read/write/search`，让 Agent 在对话中主动检索/沉淀知识。
- **P2 — 向量检索增强**：本地 `sentence-transformers` embedding + 轻量向量索引，提升 `prefetch` 召回质量（注意：不引入需联网的服务，保持 offline-first）。
- **P2 — 双向同步**：Mind 外部改动回灌 Vaelis 内置记忆（需 mtime/watch，复杂度高，放最后）。

---

## 7. 验收标准（「存在 ≠ 能用」硬门槛）

1. `config.yaml` 设 `memory.provider: mind` 后启动 CLI，日志显示 `mind` provider 激活。
2. `prefetch("vaelis 项目")` 能从 `Vault/projects/vaelis/` 读回内容并打印（证据：非空 recall 文本）。
3. Vaelis 用记忆工具写一条 → `Vault/projects/vaelis/` 下出现对应 `.md`（镜像生效）。
4. 会话结束 → `Loom/raw/chat-logs/digested/<今日>/` 出现 digest 文件。
5. **回归**：在 Mind 仓库跑 `python3 Loom/scripts/verifier.py --strict` → 退出码 0（无 BLOCKER、无新增 WARN）。
6. `hermes backup` 把 `MIND_ROOT` 纳入归档。

---

## 8. 工作量估计

- **P0（provider 核心 + 护栏）**：约 1 个模块 ~350–450 行，纯 drop-in，零核心改动，低风险。
- **P1（`/mind` skill + 工具暴露）**：+~150 行。
- **P2（向量增强 + 双向同步）**：+依赖，复杂度高。

---

## 9. 风险与边界

- Mind 是用户的「第二大脑」，**高敏感外部写入**。护栏缺失会污染知识库甚至触发 verifier 阻断提交。所有写路径必须经 §5 白名单。
- `MIND_ROOT` 默认 `/Users/ciel/Mind`，应可经 env `MIND_ROOT` 或 `config.yaml` 覆盖（符合 AGENTS.md「非 secret 配置进 config.yaml」）。
- 不影响现有 `builtin` 记忆（provider 体系是「内置 + 单外部 provider」并存，见 `memory_provider.py:4-9`），Mind 作为外部 provider 叠加，不替换内置。

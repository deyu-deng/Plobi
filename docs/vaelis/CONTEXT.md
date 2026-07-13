# Plobi Vaelis

以 AI 为核心的本地桌面智能工作台。用户通过协调器 Agent 进行自然语言对话，协调器将任务分解并调度专业 Agent 并行执行，所有过程在统一界面中实时可见。

## Language

**协调器 (Master Agent)**：
理解用户需求、追问澄清、生成 plan.md、调度子 Agent 的编排者。本身不直接执行具体任务。
_Avoid_: Master、主 Agent、中央控制器

**专业 Agent (Sub-Agent)**：
执行具体任务的工作者。当前预置角色包括 Researcher、Engineer、Publisher、Musician、Videographer、Scout。
_Avoid_: 子 Agent、从属 Agent、工具 Agent

**plan.md**：
协调器生成的结构化任务计划，包含任务列表、状态追踪、依赖关系。是子 Agent 执行的权威依据。
_Avoid_: 任务单、计划书、待办列表

**轻量调度 (Fast-Track Delegation)**：
Master 通过 LangGraph 将任务路由给单个 Agent，但不生成 plan.md、不展开 Kanban 面板，以对话流中的微交互反馈替代完整状态流。
_Avoid_: 快捷路径、快速通道、简版调度

**渐进式升维 (Progressive Escalation)**：
系统默认走轻量级交互路径，允许用户事后通过 UI 动作升格为完整 plan.md 流程；仅在算力成本过高时主动确认。
_Avoid_: 升级、提升、自动升格

## Relationships

- A **Session** contains one **Master Agent** and zero or more **Sub-Agents**
- **Master Agent** produces zero or one **plan.md** per task
- A **plan.md** contains one or more **Tasks**
- Each **Task** is assigned to exactly one **Sub-Agent**
- **Fast-Track Delegation** bypasses **plan.md** but still routes through **LangGraph**

## Example dialogue

> **User:** "帮我总结一下这篇 PDF 的核心观点"
> **Master:** *[轻量调度中...]* → 调用 Reader Agent 解析 → 以自然语言回复要点
>
> **User:** "把这篇 PDF 转成 PPT"
> **Master:** "好的，这将涉及内容提取和排版设计，已为您立项。" → 生成 plan.md → Reader 提取 → Publisher 排版 → Preview Zone 渲染

## Flagged ambiguities

- "操作系统"一词在蓝图早期版本中使用，已确认这是比喻性表述，实际定位应为"智能工作台"或"Agent 控制台"。相关文档待统一更新。

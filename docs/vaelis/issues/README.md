# Issue 追踪板 — Plobi Vaelis

> 基于 AUDIT.md (2026-05-16) 拆解的垂直切片 Issue。
> 标签定义：
> - `ready-for-agent`：标准清晰，可交给 AI Agent AFK 执行
> - `needs-triage`：需要人类确认设计决策后才能执行
> - `debt-architecture`：需要人类介入做架构重构决策

---

## P0 — 阻断验收（必须先修）

| # | Issue | 标签 | Phase | 预估 |
|---|-------|------|-------|------|
| 001 | [术语一致性修复](001-terminology-consistency.md) | `ready-for-agent` | 4 | 5min |
| 002 | [SSE 端到端测试](002-sse-e2e-test.md) | `ready-for-agent` | 2 | 2-3h |

## P1 — 红线合规（1 周内）

| # | Issue | 标签 | Phase | 预估 |
|---|-------|------|-------|------|
| 003 | [SSE 首字等待加载状态](003-sse-loading-state.md) | `needs-triage` | 2 | 1-2h |
| 004 | [预览区生成中占位状态](004-preview-placeholder.md) | `needs-triage` | 4 | 3-4h |
| 005 | [Focus Mode 交互延迟测试](005-focus-mode-perf.md) | `ready-for-agent` | 1 | 2-3h |
| 006 | [LangGraph 编排闭环测试](006-langgraph-e2e-test.md) | `ready-for-agent` | 3 | 3-4h |

## P2 — 架构深化（持续）

| # | Issue | 标签 | Phase | 预估 |
|---|-------|------|-------|------|
| 007 | [替换 print() 为结构化日志](007-structured-logging.md) | `debt-architecture` | 1 | 2-3h |
| 008 | [前端代码分割](008-code-splitting.md) | `ready-for-agent` | 1 | 2-3h |
| 009 | [Store 单元测试补全](009-store-unit-tests.md) | `ready-for-agent` | 2 | 4-6h |

---

## 当前进行中

*无*

## 已完成

*无*

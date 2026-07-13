---
id: 001
title: 术语一致性修复 — "子 Agent" → "专业 Agent"
status: ready-for-agent
priority: P0
phase: 4
---

## 问题

CONTEXT.md 明确定义了领域语言，但代码库中仍在使用被禁止的别名 "子 Agent"。这会导致：
1. LLM 模板输出混入不一致术语，污染对话体验
2. 新开发者阅读代码时形成错误心智模型

## 出现位置

- `backend/agents/master.py:176,184` — plan.md 模板中的任务标题
- `backend/main.py:252` — 注释
- `backend/agents/graph.py:3,48,79,103,122` — 注释和 docstring
- `backend/agents/base_agent.py:2` — 模块注释
- `src/i18n/zh.ts:84` — 中文翻译文案

## 验收标准

- [ ] 所有文件中 "子 Agent" 被替换为 "专业 Agent"
- [ ] `grep -r "子 Agent" backend src` 返回空结果
- [ ] i18n 英文翻译同步更新（如有对应文案）
- [ ] 不改动任何运行时逻辑，仅文本替换

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | 无（纯文本） |
| API | 无 |
| UI | `src/i18n/zh.ts` 文案更新 |
| Test | `grep` 断言验证 |

## 预估工作量

5 分钟（纯文本替换）

---
id: 004
title: '预览区生成中占位状态'
status: needs-triage
priority: P1
phase: 4
---

## 问题

Blueprint 红线："预览区在成品完成前完全空白"是绝对不能接受的。当前 `PreviewRouter` 仅在文件已存在时才能渲染。当 Agent 正在生成文件（如 Publisher 写 Markdown、Musician 生成音频）时，预览区显示"选择文件以预览"——等效于空白。

## 目标

为"文件生成中"状态提供有意义的占位 UI，让用户感知进度。

## 验收标准

- [ ] 当 focused Agent 有正在运行的任务且该任务有 outputFiles 目标时，预览区显示"正在生成 {fileName}..."
- [ ] 显示当前任务进度（如 TaskKanban 中的状态）
- [ ] 文件生成完成后，自动切换到文件预览（无需用户手动刷新）
- [ ] 生成失败时，预览区显示错误状态和重试选项

## 设计决策待确认

1. 占位 UI 样式：简单文本 + 进度条，还是模拟文件结构的骨架屏？
2. 是否支持流式预览（如 Markdown 随着生成逐段渲染）？
3. 多文件输出的 Agent 如何切换预览？

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | `TaskState` 可能需要添加 `outputFile` 预声明字段 |
| API | SSE `agent_progress` 事件携带 `outputFile` 预览信息 |
| UI | `PreviewRouter.tsx` 新增 `streamingFile` 模式 |
| Test | 组件测试：模拟任务运行中，断言占位 UI 渲染 |

## 预估工作量

3-4 小时（含流式预览评估）

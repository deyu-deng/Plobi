---
id: 005
title: 'Focus Mode 交互延迟测试'
status: ready-for-agent
priority: P1
phase: 1
---

## 问题

Blueprint 红线："双击 Agent 卡片进入 Focus Mode 有明显卡顿"是绝对不能接受的。当前 `FocusMode.tsx` 使用 framer-motion spring 动画，但缺乏量化数据证明切换流畅。

潜在卡顿点：
1. `TaskKanban` 首次挂载需要渲染全部任务卡片
2. `PreviewRouter` 需要读取最新文件内容
3. framer-motion spring 动画在低端设备上可能掉帧

## 目标

建立可重复的交互延迟测试，量化 Focus Mode 切换性能。

## 验收标准

- [ ] Playwright 测试：模拟双击 Agent 卡片
- [ ] 断言：从点击到 `FocusMode` 组件完全渲染 < 300ms
- [ ] 断言：动画期间无掉帧（或记录基准值供后续对比）
- [ ] 测试覆盖 10 个任务的场景（验证任务列表长度对性能的影响）

## 技术方案

- Playwright + `performance.mark()` / `performance.measure()`
- 在 `FocusMode` 挂载点和动画完成回调中打标记
- 或使用 Chrome DevTools Protocol 获取渲染时间线

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | 无 |
| API | 无 |
| UI | `FocusMode.tsx` 添加性能标记（开发时） |
| Test | `tests/focus-mode.perf.spec.ts` Playwright 性能测试 |

## 预估工作量

2-3 小时（含 Playwright 环境配置）

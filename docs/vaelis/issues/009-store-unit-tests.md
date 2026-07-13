---
id: 009
title: Zustand Store 单元测试补全
status: ready-for-agent
priority: P2
phase: 2
---

## 问题

所有 Zustand Store（`chatStore`、`taskStore`、`agentStore`、`layoutStore`、`settingsStore`、`resourceStore`）均无单元测试。Store 是前端状态管理的核心，其可靠性直接影响用户体验。

## 目标

为核心 Store 编写单元测试，覆盖：
1. 状态初始化
2. Action 状态转换
3. Selector 派生状态
4. 与后端 API 的异步交互（mock）

## 验收标准

- [ ] `chatStore`：测试会话 CRUD、消息追加、流式 token 追加
- [ ] `taskStore`：测试任务状态流转（pending → running → done/error）
- [ ] `agentStore`：测试 Agent 列表加载、状态更新
- [ ] `settingsStore`：测试配置检查、端口切换
- [ ] 所有测试不依赖真实后端（使用 `msw` 或手动 mock `fetch`）
- [ ] 测试在 `npm test` 中可运行

## 技术方案

- 使用 Vitest（与 Vite 项目天然集成）
- mock `fetch` 或提取 API 层为可注入的 service
- 使用 `@testing-library/react` 的 `renderHook` 测试 hook 形式的 store

```ts
// 示例：chatStore 测试
import { useChatStore } from "../store/chatStore";

test("appendToken updates message content", () => {
  const { addMessage, appendToken } = useChatStore.getState();
  addMessage("session-1", { id: "msg-1", content: "", ... });
  appendToken("session-1", "msg-1", "Hello");
  const msg = useChatStore.getState().messages["session-1"][0];
  expect(msg.content).toBe("Hello");
});
```

## 阻塞项

- 需要确认 Vitest 是否已安装
- `chatStore` 等可能依赖 `window` / `localStorage`，需要测试环境兼容

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | Store 状态类型定义（作为测试的契约） |
| API | mock 后端 API 响应 |
| UI | 无（纯逻辑测试） |
| Test | `src/store/__tests__/*.test.ts` |

## 预估工作量

4-6 小时（6 个 Store × 平均 1 小时）

---
id: 002
title: '[Tracer] SSE 端到端测试 — 用户消息到首字渲染'
status: completed
priority: P0
phase: 2
---

## 问题

整个项目**零测试覆盖**。最核心的用户路径——发送消息并看到 AI 回复——没有任何自动化验证。这意味着任何后续重构（如替换 SSE 为 WebSocket、修改消息存储结构）都可能在不知情的情况下破坏核心体验。

## 目标

建立第一个端到端测试，锁定 "用户发送消息 → 后端接收 → SSE 流式返回 → 前端渲染首字" 的完整路径。

## 验收标准

- [x] 测试能启动后端（使用 ASGITransport + AsyncClient）
- [x] 测试发送 POST /chat/send 并订阅 SSE /chat/stream
- [x] 测试断言：在 5 秒内收到至少一个 `type: "token"` 事件
- [x] 测试断言：完整 token 内容包含预期文本（"Hello"/"world"）
- [x] 测试断言：收到 `type: "done"` 终止事件
- [x] 测试在本地可运行（`python -m pytest backend/tests/test_sse_e2e.py`）

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | 无 |
| API | 添加 `backend/tests/test_sse_e2e.py` |
| UI | 无（纯后端集成测试） |
| Test | 新建端到端测试，使用 `httpx.SSE` 或 `requests` 消费流 |

## 技术选型

- 使用 `pytest` + `httpx` + `pytest-asyncio`
- 后端启动方式：`TestClient(app)`（FastAPI 原生支持 SSE 测试）
- 或使用 `asyncio.create_task` 启动后台图执行，主测试消费队列

## 阻塞项

- 需要先确定测试框架（pytest 是否已安装）
- 需要 mock LLM 调用（否则测试依赖外部 API key 和网络）

## 预估工作量

2-3 小时（含 mock 基础设施搭建）

---
id: 006
title: '[Tracer] LangGraph 编排闭环测试'
status: ready-for-agent
priority: P1
phase: 3
---

## 问题

Phase 3 的核心价值——LangGraph 多 Agent 编排——没有任何自动化测试覆盖。`plan.md` 生成、任务分配、子 Agent 执行、结果聚合这一完整链条的可靠性完全依赖人工点击验证。

## 目标

建立 LangGraph 编排的端到端测试，验证：
1. Master Agent 能根据用户输入生成有效 plan.md
2. plan.md 能被正确解析为任务列表
3. 任务被正确分配给对应的专业 Agent
4. Agent 执行结果被正确聚合
5. 最终输出通过 SSE 推送到前端

## 验收标准

- [ ] 测试使用 mock LLM 响应（避免依赖外部 API）
- [ ] 测试断言：plan.md 生成后，任务列表长度 > 0
- [ ] 测试断言：每个任务被分配到正确的 Agent ID
- [ ] 测试断言：所有任务完成后，聚合节点被触发
- [ ] 测试断言：SSE 队列中收到 `type: "done"` 事件

## 技术方案

- Mock `router.invoke()` 返回预定义的 plan.md 文本和 Agent 执行结果
- 使用 `asyncio.Queue` 捕获 SSE 事件，替代真实 HTTP 连接
- 在 `backend/tests/test_langgraph_e2e.py` 中实现

## 阻塞项

- 需要先确定 mock 策略（patch `router.invoke` 还是注入 mock provider）
- 需要稳定的 plan.md 解析器接口

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | plan.md 解析结果的数据结构 |
| API | LangGraph 执行引擎的测试入口 |
| UI | 无（纯后端测试） |
| Test | 新建 `backend/tests/test_langgraph_e2e.py` |

## 预估工作量

3-4 小时（含 mock 基础设施）

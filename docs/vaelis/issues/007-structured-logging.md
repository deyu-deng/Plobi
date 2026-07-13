---
id: 007
title: 替换 print() 为结构化日志模块
status: debt-architecture
priority: P2
phase: 1
---

## 问题

后端所有模块使用 `print()` 输出日志，导致：
1. 无法分级过滤（DEBUG/INFO/WARNING/ERROR）
2. 无法配置输出目标（文件 / stderr / 结构化日志收集器）
3. 生产环境日志混为一谈，排查问题困难

涉及文件：
- `backend/main.py`
- `backend/memory/db.py`
- `backend/agents/master.py`
- `backend/rag/indexer.py`
- `backend/api/resources.py`
- `backend/sandbox/detect_tools.py`

## 目标

引入 Python 标准库 `logging` 模块，统一后端日志规范。

## 验收标准

- [ ] 新增 `backend/logger.py`：配置根 logger、格式化器、级别
- [ ] 所有 `print()` 被替换为对应的 `logger.info/debug/warning/error`
- [ ] 日志格式包含时间戳、模块名、日志级别、消息
- [ ] 通过环境变量 `LOG_LEVEL` 可控制日志级别（默认 INFO）
- [ ] 不影响现有日志内容的可读性

## 设计决策待确认

1. 日志格式：纯文本还是 JSON（为后续结构化日志收集做准备）？
2. 是否添加日志文件轮转（`RotatingFileHandler`）？
3. 第三方库（uvicorn、chroma）的日志级别如何控制？

## 垂直切片

| 层级 | 变更 |
|------|------|
| Schema | 无 |
| API | 全部 `.py` 文件导入 logger |
| UI | 无 |
| Test | 验证 logger 配置正确加载 |

## 预估工作量

2-3 小时

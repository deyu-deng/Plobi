# aigw Python 质量基线报告

> 建立日期:2026-07-16 | 负责人:资深开发工程师
> 目标:为 aigw(Python 网关)补齐"零把关"缺口,建立可持续的质量底座。

## 一、已建立的工具链

| 工具 | 用途 | 配置位置 |
|---|---|---|
| **ruff** (lint+format) | 替代 flake8/black/isort,单工具搞定 | `aigw/pyproject.toml` `[tool.ruff.*]` |
| **mypy** | 静态类型检查(当前 lenient 模式) | `aigw/pyproject.toml` `[tool.mypy]` |
| **pytest** + pytest-asyncio | 测试运行 | `aigw/pyproject.toml` `[tool.pytest.ini_options]` |
| **.editorconfig** | 跨编辑器统一换行/缩进/编码 | 仓库根 `.editorconfig` |

本地一键装齐:`cd aigw && pip install -e ".[dev]"`

## 二、日常三道命令(团队每人必会)

```bash
cd aigw
ruff check .            # 静态检查
ruff format .           # 格式化(纯风格,零行为风险)
mypy aigw               # 类型检查(仅查有标注的代码)
pytest -q               # 跑测试
```

## 三、基线前后对比

| 指标 | 改造前 | 现在 | 说明 |
|---|---|---|---|
| ruff lint 问题数 | **114** | **48** | 自动修复 77 + 人工清死代码/冗余 ignore |
| ruff 未格式化文件 | **30** | **0** | 37 文件全部合规 |
| mypy 错误(宽松) | 19 | 13 | 清掉 6 处冗余 `# type: ignore` |
| pytest | **3 测试收集即崩** | **5 全过** ✅ | 修了缺失的 `p` fixture(真 bug) |

## 四、本次修掉的真问题

1. **`tests/test_antigravity.py` 崩了**:引用 fixture `p` 却从未注册 → 3 个测试根本没在跑(所谓"全绿"不实)。补 `@pytest.fixture def p()` 修复。
2. **3 个死变量**(F841):`cli.py` 的 `now`、`workbuddy.py` 的 `buf`、`mitm_discover.py` 的 `prof` —— 赋值后从未读取。
3. **6 处冗余 `# type: ignore`**:mypy `warn_unused_ignores` 抓出,因 `ignore_missing_imports=true` 已冗余。

## 五、剩余 48 个 ruff 问题(爬坡清单)

| 规则 | 数量 | 性质 | 优先级 |
|---|---|---|---|
| **B904** | 17 | 异常缺 `raise ... from err` 链 | **高**(网关排障关键) |
| E402 | 12 | 导入不在文件头(部分故意) | 中(需人工判断) |
| SIM105 | 5 | 可用 `contextlib.suppress` | 低 |
| RUF012 | 4 | 可变类属性应标 `ClassVar` | 低 |
| SIM102 / B007 / RUF* / B905 / ASYNC240 / I001 | 10 | 风格/小优化 | 低 |

**分布**:`test_cli_provider.py`(9)、`providers/cli.py`(7)、`main.py`(6)、`tokens/manager.py`(3)、`antigravity.py`(3) 等。

## 六、后继策略(待拍板)

采用 **ratchet(棘轮)** 策略:下一步上 **pre-commit + CI 质量门禁**(`quality.yml`),PR 时自动跑 `ruff check` + `ruff format --check` + `mypy` + `pytest`,**只挡新增违规**;存量 48 个随日常 PR 逐步还债(每 PR 顺手清几个,优先 B904)。这样质量不靠自觉,且不会一次性制造巨大 diff。

> 详见 `engineering-uplift-plan.md`(四层提升方案)。

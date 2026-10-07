# 仓内关系网（自动生成 · 勿手改 · 陈旧即红）

生成器：`scripts/build_repo_map.py` · 重生成：`uv run --extra dev python scripts/build_repo_map.py`
校验：`scripts/check_arch_gates.py` 每次提交重算 Python 半并逐字节比对；TS 半要显式 `--with-ts`（原因见生成器 docstring 旁边的注释）。

**覆盖范围**：只覆盖 `Code` 仓内根，不含 `$MIND_ROOT` 那个独立 vault。
路径都是仓内相对 + 正斜杠；块按「入边降序，出边降序，名字升序」排。

<!-- BEGIN meta -->
## 口径与盲区自陈

- 块 = 仓根带 `__init__.py` 的包根 + 仓根不带它的散 `.py`，各算一块；包根 11 + 散模块 17，撞名合并后 27 块。模块节点 2805，模块级边 7571。
  散模块的**出边**由本生成器跑一层 ast 补（grimp 只扫包，扫不到它们当引用方）；入边取 grimp 视图。
- `入 / 出` = 指向本块 / 本块指出的边数（含块内部）；`内` = 其中两端同块的那部分。
  热点按含块内的总边数排；**只有一个文件的块不列热点**（概览行就是它的全部）。
- 盲区（静态图天生看不见；处数当场数，范围 = 全仓 tracked `.py`，用 `git grep`）：
  - `__import__(` 101 处
  - `importlib.import_module` 125 处
  - `importlib.resources` 0 处
  - 名字与包根撞车的仓根散模块：plobi（`import` 时包赢，图里并成一个节点；这些文件本身只能当入口脚本跑）
  - 动态 `import` / 运行时拼出来的模块名 / 注册表驱动的分发，一律看不见——影响面下钻交给 Serena。
<!-- END meta -->

<!-- BEGIN py_overview -->
## Python 块级概览

| 块 | 入 | 出 | 内 | 主要指向（块，边数） |
|---|---:|---:|---:|---|
| plobi_cli | 1995 | 933 | 544 | agent 132, tools 77, plobi_constants 48 |
| gateway | 1337 | 375 | 162 | plobi_cli 76, tools 45, agent 38 |
| agent | 1236 | 504 | 254 | plobi_cli 95, tools 68, plobi_constants 29 |
| tools | 1128 | 488 | 233 | plobi_cli 78, agent 66, plobi_constants 39 |
| plobi | 392 | 155 | 129 | plobi_cli 12, plobi_constants 10, plobi_state 2 |
| plugins | 360 | 205 | 77 | tools 32, plobi_cli 30, agent 26 |
| plobi_constants | 243 | 1 | 0 | plobi_cli 1 |
| run_agent | 177 | 60 | 0 | agent 36, tools 9, plobi_cli 7 |
| cli | 114 | 95 | 0 | plobi_cli 43, agent 24, tools 15 |
| plobi_state | 110 | 2 | 0 | agent 1, plobi_constants 1 |
| utils | 91 | 0 | 0 | — |
| cron | 89 | 57 | 10 | plobi_cli 13, agent 8, gateway 8 |
| tests | 64 | 4498 | 64 | gateway 1077, plobi_cli 1040, tools 607 |
| tui_gateway | 47 | 106 | 9 | plobi_cli 37, agent 32, tools 15 |
| model_tools | 46 | 14 | 0 | tools 8, plobi_cli 3, acp_adapter 1 |
| toolsets | 37 | 2 | 0 | gateway 1, tools 1 |
| providers | 32 | 4 | 1 | plobi_cli 2, plobi_constants 1 |
| acp_adapter | 30 | 42 | 11 | agent 9, plobi_cli 9, tools 6 |
| plobi_logging | 12 | 5 | 0 | plobi_cli 2, agent 1, plobi_constants 1 |
| plobi_bootstrap | 10 | 1 | 0 | tools 1 |
| plobi_time | 9 | 3 | 0 | plobi_cli 2, plobi_constants 1 |
| trajectory_compressor | 4 | 5 | 0 | agent 2, plobi_cli 1, plobi_constants 1 |
| batch_runner | 2 | 6 | 0 | model_tools 1, plobi_bootstrap 1, run_agent 1 |
| mcp_serve | 2 | 3 | 0 | plobi_constants 1, plobi_state 1, tools 1 |
| toolset_distributions | 2 | 1 | 0 | toolsets 1 |
| mini_swe_runner | 1 | 5 | 0 | tools 3, agent 2 |
| setup | 0 | 0 | 0 | — |
<!-- END py_overview -->

<!-- BEGIN py_hotspots -->
## Python 块内热点（fan-in / fan-out 前 5）

**plobi_cli**
  入顶 config.py 300 · auth.py 121 · main.py 94 · plugins.py 87 · models.py 67
  出顶 main.py 147 · web_server.py 100 · cli_commands_mixin.py 38 · doctor.py 32 · tools_config.py 30
**gateway**
  入顶 config.py 348 · platforms/base.py 221 · run.py 202 · session.py 169 · session_context.py 44
  出顶 run.py 116 · slash_commands.py 51 · platforms/api_server.py 26 · platforms/base.py 13 · platforms/qqbot/adapter.py 11
**agent**
  入顶 auxiliary_client.py 89 · model_metadata.py 54 · anthropic_adapter.py 40 · redact.py 38 · context_compressor.py 37
  出顶 conversation_loop.py 37 · agent_init.py 35 · agent_runtime_helpers.py 32 · auxiliary_client.py 32 · tool_executor.py 22
**tools**
  入顶 registry.py 90 · terminal_tool.py 65 · environments/local.py 51 · mcp_tool.py 47 · approval.py 45
  出顶 browser_tool.py 24 · terminal_tool.py 23 · mcp_tool.py 19 · send_message_tool.py 19 · delegate_tool.py 18
**plobi**
  入顶 agenda/service.py 41 · agents/registry.py 36 · agenda/store.py 27 · collectors/chatlog/pipeline.py 19 · collectors/chatlog/config.py 18
  出顶 agents/registry.py 15 · collectors/chatlog/pipeline.py 11 · console/router.py 10 · collectors/chatlog/watchdog.py 7 · agenda/checkin.py 6
**plugins**
  入顶 __init__.py 171 · memory/honcho/client.py 13 · memory/__init__.py 9 · memory/honcho/__init__.py 7 · memory/honcho/session.py 7
  出顶 memory/honcho/cli.py 12 · memory/hindsight/__init__.py 9 · memory/openviking/__init__.py 9 · memory/honcho/__init__.py 8 · teams_pipeline/cli.py 8
**cron**
  入顶 jobs.py 38 · scheduler.py 32 · scheduler_provider.py 9 · suggestions.py 4 · lifecycle_guard.py 3
  出顶 scheduler.py 36 · jobs.py 8 · scheduler_provider.py 4 · suggestions.py 4 · __init__.py 2
**tests**
  入顶 docker/conftest.py 15 · gateway/restart_test_helpers.py 10 · plobi_cli/conftest_dashboard_auth.py 9 · gateway/relay/stub_connector.py 6 · tools/conftest.py 6
  出顶 plobi_cli/test_subcommands_batch.py 25 · plobi_cli/test_web_server.py 21 · test_tui_gateway_server.py 19 · run_agent/test_run_agent.py 17 · test_windows_subprocess_no_window_flags.py 15
**tui_gateway**
  入顶 server.py 26 · entry.py 5 · git_probe.py 3 · loop_noise.py 2 · project_tree.py 2
  出顶 server.py 91 · entry.py 6 · slash_worker.py 4 · ws.py 3 · git_probe.py 1
**providers**
  入顶 __init__.py 25 · base.py 7
  出顶 __init__.py 2 · base.py 2
**acp_adapter**
  入顶 edit_approval.py 5 · session.py 5 · entry.py 4 · tools.py 4 · auth.py 3
  出顶 server.py 18 · entry.py 8 · session.py 6 · events.py 4 · edit_approval.py 2
<!-- END py_hotspots -->

<!-- BEGIN ts_pending -->
## TS 块级概览与热点（本轮留空 · 待第二块）

本段是**占位**：四个 workspace 成员（`apps/desktop`、`apps/shared`、`ui-tui`、`web`）与 `apps/desktop/src` 一级子块的 dependency-cruiser 图**还没并进来**（待第二块，跑 `scripts/build_repo_map.py --with-ts` 才会生成这两段）。
所以本图现在只等于 Python 半：前端那半在这里一条边都没有，别读成「前端谁都不依赖」。
同为第二块的还有闸门接线：上面那句「每次提交重算并逐字节比对」是这一刀接线后的契约，在 `check_arch_gates.py` 真调用本生成器之前，本图不由任何钩子校验，改代码不会自动变红。
<!-- END ts_pending -->

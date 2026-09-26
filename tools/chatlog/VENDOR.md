---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 67e28a0cadea4c0368daeffdef023791_e234418ca3cf11f1bc17525400826444
    ReservedCode1: IqYaFfSTLeW9cct05YfkycAvEYBdYnudDclzsOs/2j84uN+b9DbeUvu47n/NyJOOIe0e5xi/HrDni0caWPPCbontqKHdu1eSRMNBUrvO20WfFC3b1Vvl1JlyE06Btx3cVpGde6qGkrU3lmMm4I5u993IC+G0gxmalhmgmHXIbYbxDp8D+wig8QTaZRU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 67e28a0cadea4c0368daeffdef023791_e234418ca3cf11f1bc17525400826444
    ReservedCode2: IqYaFfSTLeW9cct05YfkycAvEYBdYnudDclzsOs/2j84uN+b9DbeUvu47n/NyJOOIe0e5xi/HrDni0caWPPCbontqKHdu1eSRMNBUrvO20WfFC3b1Vvl1JlyE06Btx3cVpGde6qGkrU3lmMm4I5u993IC+G0gxmalhmgmHXIbYbxDp8D+wig8QTaZRU=
---

# VENDOR: chatlog (imldy fork)

本目录是上游 `imldy/chatlog` 的 vendor 快照，供 Plobi 采集层（MVP 微信消息采集）使用。

## 来源信息

| 项 | 值 |
|---|---|
| 上游仓库 | https://github.com/imldy/chatlog.git |
| 上游项目 | sjzar/chatlog 的 imldy fork（纯 CLI，无 GUI，本地数据库直读解密） |
| Vendor 时点 commit | `a7162bc` (`a7162bca9454fa43b5950a2414670983fe180e56`) |
| Vendor 日期 | 2026-08-30 |
| 上游提交说明 | fix dat2img (#302) |

## Vendor 内容约定

- 复制自 `D:\Tools\wechat\chatlog-src-imldy`，**排除 `.git` 目录**、排除二进制构建产物（dist/build/venv 等）。
- 本目录为纯源码快照，**不包含预编译二进制**；编译产物应放 `bin/`（已被 `.gitignore` 忽略）。
- 修改本目录内文件需谨慎：源码改动会与上游同步流程冲突，若需定制请在 Plobi 侧封装脚本（见 `scripts/chatlog_server.ps1`），尽量不改上游源码。

## 构建方式（Windows）

```powershell
cd D:\Projects\Plobi\Code\tools\chatlog
go build -o bin\chatlog.exe ./cmd/chatlog
```

## 微信更新后的同步流程

1. **检查上游新提交**：
   ```powershell
   cd D:\Tools\wechat\chatlog-src-imldy
   git fetch origin
   git log --oneline HEAD..origin/main   # 查看本地 vendor commit 之后的新提交
   ```
2. **评估变更**：查看新提交涉及的文件与说明，确认是否影响 Plobi 采集链路（server 子命令参数、HTTP API、解密逻辑等）。
3. **同步 vendor**：
   ```powershell
   # 将上游目录更新到目标 commit（例如 origin/main 的 HEAD）
   cd D:\Tools\wechat\chatlog-src-imldy
   git pull --ff-only origin main

   # 复制到 Plobi 仓库（排除 .git 与二进制产物）
   robocopy D:\Tools\wechat\chatlog-src-imldy D:\Projects\Plobi\Code\tools\chatlog /E /XD "<src>\.git" /NFL /NDL /NJH /NJS /NP
   ```
4. **更新本文件**：将「Vendor 时点 commit」与「Vendor 日期」改为新 commit 与日期，必要时补充变更说明。
5. **重新构建并冒烟**：
   ```powershell
   cd D:\Projects\Plobi\Code\tools\chatlog
   go build -o bin\chatlog.exe ./cmd/chatlog
   # 用 scripts\chatlog_server.ps1 拉起服务，验证 /api/v1/session 等接口
   ```
6. **提交 Plobi 仓库**（git add tools/chatlog + scripts/chatlog_server.ps1 + VENDOR.md），并运行现有回归/审计检查。

## 运行方式

HTTP 服务由 `D:\Projects\Plobi\Code\scripts\chatlog_server.ps1` 拉起，监听 `127.0.0.1:5030`，默认开启 auto-decrypt，密钥从环境变量读取（禁止硬编码）。详见脚本头部注释。
*（内容由AI生成，仅供参考）*

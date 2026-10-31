---
name: office-cli
description: Use OfficeCLI to read, edit, validate and render local DOCX/XLSX/PPTX files directly. Use for Office edits the user asked for, including files currently open in Plobi; Plobi auto-reloads clean editors and protects unsaved human edits. AI edits the real file, not a proposal.
version: 0.2.0
author: Plobi
license: Apache-2.0
metadata:
  plobi:
    tags: [office, docx, xlsx, pptx, direct-edit]
---

# OfficeCLI：直接改 Office 文件

## 先找到它（每次会话第一件事）

`officecli` 是外部 CLI（Apache-2.0，单文件）。按顺序试，第一个能跑的就用它：

1. `officecli --version` —— 在 PATH 里就直接用。
2. `$PLOBI_HOME/bin/officecli.exe`（Windows）或 `$PLOBI_HOME/bin/officecli`（macOS/Linux）—— 产品自带的落点。`$PLOBI_HOME` 就是 Plobi 家目录。
3. 都没有：问用户装在哪，或者请他装一个。

**找不到就停下来说清楚**：「本机没有 officecli，装好后我才能改 Office 文件」，并给出安装地址。不要用别的库硬凑，不要假装改过。

用 **terminal** 工具调用它；不要把它注册成 MCP 的单个 `command` 工具（那是选分支的元工具，违反裁 185）。

## 先读再改

1. 先加载该格式的官方工作手册：`officecli load_skill excel`（或 `word` / `pptx`）。这一步不是可选的。
2. 读结构：`officecli view <file> outline` / `stats` / `issues`；读单元格：`officecli get <file> "/<sheet>/A1" --json`。
3. 改之前先留快照（同目录 `.<name>.plobi-backup-<timestamp>`，用 `cp`），再动文件。

## 改

- 一个格：`officecli set <file> "/中文论文/A1" --prop value="..."`。
- 多个改动用 `officecli batch <file> --commands <json>`，一次打开/保存，别循环 set。
- 路径从 1 开始；表路径形如 `/Sheet1/A1`，Word 是 `/body/p[1]`，PPT 是 `/slide[1]/shape[2]`。
- 写完 `officecli save <file>`；之后才让别的程序读磁盘。

## 验完再交

- `officecli validate <file>` 干净。
- `officecli view <file> issues` 干净；再扫 `view <file> text` 有没有 `TODO`/`xxxx`/`{{...}}`。
- 版式重要时 `officecli view <file> screenshot --page N`，自己看一遍再交。
- 失败就修完重验；不要说“应该可以”。

## 和打开的 Plobi 编辑器同步

- Plobi 每 1.5s 比对打开文件的磁盘版本。文本/CSV 无未保存改动会自动重载；GenOffice 的 Office 页签会先问自己的脏状态：**干净就自动重载**，有未保存修改才弹「文件已在磁盘更新」条。编辑器自己的保存带外部改动检查（`errDiskChanged` / `external-modified`），不会静默覆盖你写的内容。
- 你可以**直接改正在 Plobi 里打开的文件**，不必等用户关闭。改完先 `officecli save`，再用 `list_open_files` 看这个文件：
  - `dirty=false`：编辑器会自动重载，直接回报改了什么即可。
  - `dirty=true`：人还有未保存修改，先停下让用户保存或放弃，再改；不要硬覆盖。
  - `dirty=null`：编辑器没应答，改完告诉用户看「文件已在磁盘更新」条并点重新加载。
- 只改用户点名的文件或它所在的工作区；不要扫盘、不要碰系统文件。
- 你改了什么、备份在哪、验过哪几条，一起回报给人。

# 发布说明（Release notes）

这一目录是 GitHub 上各版本发布页正文的**仓内真源**。

> **为什么落在这里**：发布页正文是**那个版本的一部分**——tag 钉住了代码，也得钉住这一版
> 真正发布出去的正文，所以它必须和代码同仓、同一个 tag（仓内门只许 `docs/plobi/` 这棵子树；
> 项目级文档归仓外 `../Docs/`）。

## 规矩

- **一版一份**：中文正文 `vX.Y.Z.md`，英文正文 `Plobi-X.Y.Z-release-notes.en.md`。
- **正文只写用户能感知到的变化**：这一版有什么新东西、修了什么毛病。内部信息（裁定号、WP 号、
  改名与撤版的过程、流水线与凭据）不进正文。
- **只写这一版真有的**。写之前对着该版本的 tag 核一遍代码或界面，别照抄提交标题——提交标题里
  常有当周才发生的事，而发布页说的是这一版的成品。
- **语气照行业标杆**（VS Code / GitHub Desktop 那套）：短句、主动语态、结果导向，不写营销形容词，
  不贴 commit log 当更新日志。分类用「新增 / 改进 / 修复」，空分类直接删。

## 语域：这是发布页，不是聊天消息

标杆的写法是**分类 → 一行一条 → 名词短语 + 事实**：

```
### 新增

- **知识库** — 左栏新增知识库入口。笔记支持检索、大纲与就地编辑。
```

具体几条：

- **小标题是名词短语**，不是「新增了什么」这类口语标题。
- **每条以 `**名词短语** — ` 起头**，破折号后面才是事实。一条一行，不写长段落。
- **不称呼读者**。不用「你可以」「你需要」，也不写「我们」。发布页在陈述这一版是什么，
  不在对读者说话。
- **不写营销形容词**：强大、丝滑、无缝、一站式、惊艳、前所未有这类词一律不进正文。
- **不用口语与比喻**：随手、当场、说人话、一片红灯、摆在那儿这类写法不进正文。
  「技能可以从经验里长出来」这类句子同理——写「技能可从历史经验中沉淀」。
- **修复写「旧行为 → 新行为」**：前半句是此前如何，后半句是现在如何。
- **技术名词原样写进反引号**：`/v1/chat/completions`、`memory_read(scope)`、`.env`、`SOUL.md`。

这些不是口味，是**门**：`scripts/release.py` 里的 `notes_register_problems()` 会在发布之前把
口语词、营销词、称呼读者的写法、内部措辞和不合形状的条目一并拦下；`tests/scripts/test_release_notes_guards.py`
则要求仓内每一份正文都能过这道门。改文案时门会先红——先让它红一次，看清判据再改。

## 双语：正文中文，英文作附件

GitHub 的发布页正文没有语言切换控件（Markdown 不支持 tab）。这里的做法是：

- **正文放中文**，发布页顶部一行 `[中文](…/releases/tag/vX.Y.Z) | [English](…/releases/download/…)`。
- **英文版作为 `.md` 附件**挂在同一个 Release 上。
- 两份是**同一套事实的两种语言**，改一份就要同步另一份。

**英文附件的文件名就是它在发布页上的资产名**（`Plobi-X.Y.Z-release-notes.en.md`）。
正文里那行链接指的就是这个名字，而 `gh` 用文件名当资产名——两边必须一模一样，
否则读者点过去是 404，而发布页看上去一切正常。

## 发版时怎么用

桌面安装包是 CI 出的：推 tag 触发 `.github/workflows/release-desktop.yml`，那条流水线自己建
Release 并挂三个平台的包。所以次序是**先推 tag 让流水线出包，再换正文**：

```bash
# 1) 五处版本号先落成要发的号
#    （plobi_cli/__init__.py、pyproject.toml、apps/desktop/package.json、
#      acp_registry/agent.json、package-lock.json）
# 2) 打 tag，只推这一枚（`--tags` 会把别人删掉的 tag 复活）
git tag -a v0.3.0 <被测的那一笔> -m "Plobi v0.3.0 (2026.11.6)"
git push origin v0.3.0
# 3) 等流水线绿：Release 上已经有三个平台的安装包
# 4) 换正文 + 挂英文附件
gh release edit v0.3.0 --notes-file docs/plobi/releases/v0.3.0.md
gh release upload v0.3.0 docs/plobi/releases/Plobi-0.3.0-release-notes.en.md
# 5) 核：正文是中文、顶部中英互跳那行点得开、英文附件能下载、三平台安装包都在
```

`scripts/release.py` 能改正文、造 Python 产物，但它 **`--publish` 会自己建 Release**，跟流水线里
那句 `gh release create` 撞车 ⇒ 谁先到谁把对方顶掉，而输的那条通常带着安装包。要它参与就别带
`--publish`。

它自带的三道保险，都在发布之前拦：

- `--notes-file` 给的正文必须**是这一版**的：文件名里的版本和这一轮要发的号对不上，直接停。
  （版本号读的是仓里的 `__version__`，不是最后一个 tag——`--bump minor` 会把它往前挪一格。）
- `--notes-en-file` 给的英文附件按 `Plobi-X.Y.Z-release-notes.en.md` 这个名字挂上去，
  与正文里那行链接对得上。
- 正文里链到的发布页／附件只要对不上这一版、或那个附件不会存在，停。

```bash
# 干跑：看这一版真正会发出去的正文长什么样
python scripts/release.py --notes-file docs/plobi/releases/v0.3.0.md \
    --notes-en-file docs/plobi/releases/Plobi-0.3.0-release-notes.en.md
```

# 发布说明（Release notes）

这一目录是 GitHub 上各版本发布页正文的**仓内真源**。

## 规矩

- **文件名 = 版本号**（`v0.2.0.md`），一版一份；正文里第一行是版本标题。
- **正文只写用户能感知到的变化**：这一版有什么新东西、修了什么毛病。内部信息（裁定号、WP 号、
  改名与撤版的过程、流水线与凭据）不进正文。
- **只写这一版真有的**。写之前对着该版本的 tag 核一遍代码或界面，别照抄提交标题——提交标题里
  常有当周才发生的事，而发布页说的是这一版的成品。
- **语气照行业标杆**（VS Code / GitHub Desktop 那套）：短句、主动语态、结果导向，不写营销形容词，
  不贴 commit log 当更新日志。分类用「新增 / 改进 / 修复」，空分类直接删。

## 双语

GitHub 的发布页正文没有语言切换控件（Markdown 不支持 tab）。这里的做法是：

- **正文放中文**，发布页顶部一行 `[中文](…/releases/tag/vX.Y.Z) | [English](…/releases/download/…)`。
- **英文版作为 `.md` 附件**挂在同一个 Release 上（`Plobi-vX.Y.Z-release-notes.en.md`），链接从
  `releases/download` 指过去。
- 两份是**同一套事实的两种语言**，改一份就要同步另一份。

## 发版时怎么用

```bash
python scripts/release.py --bump minor --publish --notes-file docs/releases/v0.3.0.md
```

`--notes-file` 给的正文当发布页正文；自动生成的 commit log 不丢，降级成附件一起挂上去
（`Changelog` 那个附件）。不给这个选项，发布页正文就是自动生成的 commit log——**那不是给人看的东西**，
所以发版前先写好这一份。

英文附件自己传：

```bash
gh release upload vX.Y.Z docs/releases/vX.Y.Z.en.md#Release%20notes
```

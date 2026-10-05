#!/usr/bin/env bash
# check-docs.sh — pre-commit hook: 文档纪律强制校验
#
# 项目级文档真源不在本仓，在仓外 ../Docs/（AGENTS.md「Documentation Discipline」）。
# 本仓 docs/ 只承载确需随代码走的实现级细节，且只允许 docs/plobi/ 这一棵子树。
#
# 强制规则（有牙齿，不靠自觉）：
#   R1. docs/ 下新增/改名/复制进来的文件必须落在 docs/plobi/ 下，否则拒绝提交。
#
# 覆盖所有扩展名（旧版只看 .md/.pdf，.yaml 和代码片段能溜过去）。
# 旧 R2「必须在 docs/INDEX.md 登记一行」已作废：该索引从未建立，检查因此
# 退化成 WARN 跳过，等于空转；登记归仓外 Docs/README.md，本仓 hook 无法在 CI
# 里访问 ../Docs/，所以不在这里查。
#
# 安装：cp scripts/check-docs.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# 手动跑：bash scripts/check-docs.sh

set -uo pipefail

failed=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    docs/plobi/*) continue ;;
  esac
  echo "ERROR: $f —— 本仓 docs/ 只允许 docs/plobi/ 子树；项目级文档（需求/裁定/排期/验收）写到仓外 ../Docs/ 并在 Docs/README.md 挂一行"
  failed=1
done < <(git diff --cached --name-only --diff-filter=AMRC -- docs/)

if [ "$failed" -ne 0 ]; then
  echo ""
  echo "提交被拒：docs/ 只承载 docs/plobi/ 下的实现级细节，其余文档归仓外 ../Docs/。"
  exit 1
fi

exit 0

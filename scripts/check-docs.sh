#!/usr/bin/env bash
# check-docs.sh — pre-commit hook: 文档纪律强制校验
#
# 强制两条规则（有牙齿，不靠自觉）：
#   R1. docs/ 下新增/改名的文档必须落在允许的类目子目录里。
#   R2. docs/ 下新增/改名的文档必须在 docs/INDEX.md 登记一行。
#
# 允许的类目：north_star specs adr reference audit runbooks templates plans archive plobi
# docs/INDEX.md 自身豁免。
#
# 安装：cp scripts/check-docs.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# 手动跑：bash scripts/check-docs.sh

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
INDEX="$REPO_ROOT/docs/INDEX.md"
ALLOWED=" north_star specs adr reference audit runbooks templates plans archive plobi "

failed=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    docs/*) ;;
    *) continue ;;
  esac
  [ "$f" = "docs/INDEX.md" ] && continue

  inner="${f#docs/}"
  top="${inner%%/*}"

  if [ "$top" = "$inner" ]; then
    echo "ERROR: $f 必须放在 docs/ 的类目子目录里（不允许散落在 docs/ 根部）"
    failed=1
    continue
  fi

  case "$ALLOWED" in
    *" $top "*) ;;
    *)
      echo "ERROR: $f 不在允许的文档类目（$ALLOWED）"
      failed=1
      ;;
  esac

  if [ -f "$INDEX" ]; then
    if ! grep -qF "$f" "$INDEX"; then
      echo "ERROR: $f 未在 docs/INDEX.md 登记 —— 新增/改名文档必须先在 INDEX 中加一行"
      failed=1
    fi
  else
    echo "WARN: docs/INDEX.md 缺失，跳过登记检查"
  fi
done < <(git diff --cached --name-only --diff-filter=AM | grep -E '^docs/.*\.(md|pdf)$')

if [ "$failed" -ne 0 ]; then
  echo ""
  echo "提交被拒：请把文档放入正确类目并在 docs/INDEX.md 登记后再提交。"
  exit 1
fi

exit 0

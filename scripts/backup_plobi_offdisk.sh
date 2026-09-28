#!/usr/bin/env bash
# Off-disk backup for the things that would hurt to lose (R-042).
#
# Why this exists: the Windows machine's disk died on 2026-09-27 and took the
# only copy of 223MB of runtime data with it — including a same-disk "backup".
# Only two things survived: the code's git history, and bundles. So this script
# covers the two artifacts that currently have no second copy anywhere:
#
#   1. $PLOBI_HOME (~/.plobi) — credentials, config, sessions, pets. Encrypted,
#      because auth.json holds live keys.
#   2. The Docs repo — it has NO remote, so until now its history existed on
#      exactly one platter.
#
# The destination must be off this disk (external volume, or a folder the cloud
# client syncs). Without PLOBI_BACKUP_DIR the script still writes, but into
# bundles/keep/data/ and prints a warning, because "no second copy" is the
# failure mode we are fixing — a same-disk copy must never be mistaken for one.
#
# Usage:
#   scripts/backup_plobi_offdisk.sh --init-key     # once: create the backup key
#   PLOBI_BACKUP_DIR=/Volumes/x/plobi-backup scripts/backup_plobi_offdisk.sh
#   scripts/backup_plobi_offdisk.sh --keep 7       # prune to N newest in destination
set -euo pipefail

# This script lives *inside* the Code repo on purpose: a backup tool that only
# exists on the disk it protects is the failure mode R-042 is about.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$REPO/.." && pwd)"
DATA_HOME="${PLOBI_HOME:-$HOME/.plobi}"
DOCS_REPO="$ROOT/Docs"
LOCAL_DIR="$ROOT/bundles/keep/data"
KEY_FILE="${PLOBI_BACKUP_KEY:-$HOME/.plobi-backup.key}"
DEST="${PLOBI_BACKUP_DIR:-$LOCAL_DIR}"
KEEP=14
DRY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --init-key)
      if [ -e "$KEY_FILE" ]; then
        echo "key already exists: $KEY_FILE (refusing to overwrite — that would orphan old archives)" >&2
        exit 1
      fi
      umask 077
      /usr/bin/openssl rand -hex 32 > "$KEY_FILE"
      chmod 600 "$KEY_FILE"
      cat <<EOF
created $KEY_FILE (mode 600)

The passphrase is deliberately NOT printed here — this script runs from a
terminal that logs, and from cron. Read it when you need it:
    cat $KEY_FILE

Copy it into your password manager NOW and keep it somewhere that is not this
disk. Lose it and every archive this script produces is unreadable; lose only
the disk and the archives are still useless without it.
EOF
      exit 0
      ;;
    --keep) KEEP="${2:?--keep needs a number}"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '1,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -d "$DATA_HOME" ] || { echo "no data dir at $DATA_HOME" >&2; exit 1; }
[ -d "$DOCS_REPO/.git" ] || { echo "no Docs repo at $DOCS_REPO" >&2; exit 1; }

if [ "$DEST" = "$LOCAL_DIR" ]; then
  echo "!! PLOBI_BACKUP_DIR is unset — writing to $LOCAL_DIR, which is ON THE SAME DISK." >&2
  echo "!! That is a convenience copy, not a backup. Point PLOBI_BACKUP_DIR at another" >&2
  echo "!! volume or a cloud-synced folder before you trust this." >&2
fi

if [ ! -s "$KEY_FILE" ]; then
  echo "missing key file: $KEY_FILE" >&2
  echo "run: scripts/backup_plobi_offdisk.sh --init-key   (then store the passphrase off-disk)" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M)"
PASS="$(tr -d '\n' < "$KEY_FILE")"
# Docs is on `master` while Code is on `main`; a bare `git clone bundle` resolves
# HEAD against the *clone's* default branch and reports an empty repo. Name it.
DOCS_BRANCH="$(git -C "$DOCS_REPO" symbolic-ref --short HEAD)"
mkdir -p "$DEST"

make_archive() {
  local name="$1" src="$2"
  local out="$DEST/$name"

  if [ "$DRY" = 1 ]; then
    echo "would write $out"
    return 0
  fi

  tar -C "$(dirname "$src")" \
      --exclude='logs' --exclude='*_cache' --exclude='audio_cache' --exclude='image_cache' \
      -czf - "$(basename "$src")" \
    | /usr/bin/openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "pass:$PASS" -out "$out.tmp"
  mv "$out.tmp" "$out"

  # Verify by reading it back: an unreadable archive is indistinguishable from
  # no archive, and we would only find out on the day we need it.
  /usr/bin/openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "pass:$PASS" -in "$out" \
    | tar -tzf - > /dev/null
  printf 'ok   %-46s %8s  (round-trip verified)\n' "$name" "$(du -h "$out" | cut -f1)"
}

echo "backup $DATA_HOME  →  $DEST"
make_archive "plobi-data-$STAMP.tar.gz.enc" "$DATA_HOME"

DOCS_BUNDLE="$DEST/plobi-docs-$STAMP.bundle"
if [ "$DRY" = 1 ]; then
  echo "would write $DOCS_BUNDLE"
else
  git -C "$DOCS_REPO" bundle create "$DOCS_BUNDLE" --branches --tags
  git -C "$DOCS_REPO" bundle verify "$DOCS_BUNDLE" > /dev/null
  printf 'ok   %-46s %8s  (bundle verified)\n' "plobi-docs-$STAMP.bundle" "$(du -h "$DOCS_BUNDLE" | cut -f1)"
fi

if [ "$DRY" = 0 ] && [ "$KEEP" -gt 0 ]; then
  # Prune only files this script named, newest $KEEP of each family. bundles/keep/
  # has before it a history of rotation jobs eating the only copy, so anything
  # not matching our own prefixes is left completely alone.
  for pattern in 'plobi-data-*.tar.gz.enc' 'plobi-docs-*.bundle'; do
    ( cd "$DEST" && ls -1 $pattern 2>/dev/null | sort | tail -n +$((KEEP + 1)) | while read -r old; do
        rm -f "$DEST/$old"
        echo "pruned $old"
      done )
  done
fi

cat <<EOF

Restore (destination = wherever this archive lives):
  /usr/bin/openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \\
    -pass "file:$KEY_FILE" -in plobi-data-$STAMP.tar.gz.enc | tar -xz -C ~
  git clone -b "$DOCS_BRANCH" /path/to/plobi-docs-$STAMP.bundle Docs      # or: git bundle unbundle
EOF

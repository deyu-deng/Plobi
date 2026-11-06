"""分批跑 tests/plobi，把每批的失败清单追加进 temp/sweep.txt。

单次 shell 调用有几十秒上限，全量 2000+ 用例跑不完，所以拆批 + 后台进程。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(CWD, ".venv", "Scripts", "python.exe")
OUT = os.path.join(CWD, "temp", "sweep.txt")
BATCH = 22


def files() -> list[str]:
    return sorted(glob.glob(os.path.join(CWD, "tests", "plobi", "**", "test_*.py"), recursive=True))


def main() -> int:
    fs = files()
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    if only:
        fs = [f for f in fs if only in f]
    total = len(fs)
    with open(OUT, "w", encoding="utf-8") as log:
        for start in range(0, total, BATCH):
            batch = fs[start:start + BATCH]
            t0 = time.time()
            proc = subprocess.run(
                [EXE, "-m", "pytest", *batch, "-q", "--tb=line", "-rf",
                 "-p", "no:cacheprovider", "--no-header", "-p", "no:randomly", "-n", "8"],
                cwd=CWD, capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            tail = "\n".join(
                line for line in (proc.stdout or "").splitlines()
                if line.startswith(("FAILED", "ERROR")) or " passed" in line or " failed" in line
            )
            log.write(f"### batch {start // BATCH + 1} ({len(batch)} files, rc={proc.returncode}, {time.time() - t0:.0f}s)\n")
            log.write(tail + "\n\n")
            log.flush()
        log.write("### DONE\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

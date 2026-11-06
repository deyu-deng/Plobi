"""跑 tests/plobi 的一个切片（默认每片 40 个文件），只打印失败清单与一行汇总。

shell 单次调用有时限，全量不能一次跑完；xdist 8 路并行让一片约 20-40s。
用法：python temp/run_batch.py <起始索引>
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

CWD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(CWD, ".venv", "Scripts", "python.exe")
SLICE = 40


def files() -> list[str]:
    return sorted(glob.glob(os.path.join(CWD, "tests", "plobi", "**", "test_*.py"), recursive=True))


def main() -> int:
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    fs = files()
    batch = fs[start:start + SLICE]
    t0 = time.time()
    proc = subprocess.run(
        [EXE, "-m", "pytest", *batch, "-q", "--tb=line", "-rf",
         "-p", "no:cacheprovider", "--no-header", "-p", "no:randomly",
         "-n", "10", "--timeout=120"],
        cwd=CWD, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "").splitlines()
    for line in out:
        if line.startswith(("FAILED", "ERROR")) or " passed" in line or " failed" in line:
            print(line)
    print(f"[slice {start}:{start + len(batch)} of {len(fs)} files, rc={proc.returncode}, {time.time() - t0:.0f}s]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

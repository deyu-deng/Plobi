@echo off
REM Thin Windows wrapper for scripts\backup_repos.py - the single implementation.
REM No drive letters here or there: the repo root is derived from this file's own
REM location, and the brain's location comes from MIND_ROOT (ruling 72 / 48.1).
REM Interpreter probing is not decoration - `python3` on this machine is the
REM Microsoft Store forwarder stub, which "succeeds" while backing nothing up
REM (ruling 72.7). If no interpreter runs, this exits non-zero and says so.
setlocal EnableExtensions EnableDelayedExpansion
set "HERE=%~dp0"
set "PY="
for %%P in (python3 python py) do if not defined PY (
    "%%P" -c "import sys" >nul 2>&1 && set "PY=%%P"
)
if not defined PY (
    echo FAIL: no usable Python interpreter - tried python3, python, py. Nothing was backed up. 1>&2
    exit /b 127
)
"!PY!" "%HERE%backup_repos.py" %*
set "RC=!ERRORLEVEL!"
exit /b !RC!

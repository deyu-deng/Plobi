@echo off
REM Thin Windows wrapper for scripts\sync_mind.py - the single implementation.
REM Interpreter probing is not decoration: `python3` on this machine is the
REM Microsoft Store forwarder stub, which "succeeds" while syncing nothing (the
REM same lesson run-bundle.cmd carries from ruling 72.7). No usable interpreter
REM means a non-zero exit and a loud line - a scheduled task must not read green.
REM Nothing here hardcodes a drive letter; the script reads MIND_ROOT and writes
REM its own log under PLOBI_HOME, so this shell stays a shell.
setlocal EnableExtensions EnableDelayedExpansion
set "HERE=%~dp0"
set "PY="
for %%P in (python3 python py) do if not defined PY (
    "%%P" -c "import sys" >nul 2>&1 && set "PY=%%P"
)
if not defined PY (
    echo FAIL: no usable Python interpreter - tried python3, python, py. Brain was NOT synced. 1>&2
    exit /b 127
)
"!PY!" "%HERE%sync_mind.py" %*
exit /b !ERRORLEVEL!

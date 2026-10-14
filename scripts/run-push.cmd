@echo off
rem Daily push wrapper for WP-REMOTE-PUSH-DAILY (ruling 127).
rem ASCII only on purpose: cmd reads .cmd in the OEM codepage, so UTF-8 CJK comments break parsing.
rem The redirect is required: a scheduled-task session has no visible stdout.
"D:\Projects\Plobi\Plobi-Desktop\Code\.venv\Scripts\python.exe" "D:\Projects\Plobi\Plobi-Desktop\Code\scripts\push_daily.py" %* >> "D:\Projects\Plobiundles\keep\push-cmd.log" 2>&1
exit /b %ERRORLEVEL%

# 提权侧车的启动壳 —— 由计划任务「Plobi Egress Helper」在用户登录时以最高权限拉起。
#
# 为什么这一层不是 NSIS 直接跑 python（2026-11-22 定）：
#   ① 装的时候 python 运行时**还不存在** —— venv 是 Plobi 第一次启动时才建在数据目录里的。
#      安装器把任务动作写死成 python.exe 的路径，那天登录就必定失败，而且失败得无声无息。
#   ② 数据目录是用户选的（可以在 D 盘），安装器不知道 venv 最终落在哪一格；这一格只有
#      运行时自己知道。所以任务只认「脚本在哪」，路径由脚本现算。
#   ③ 任务动作必须是一个装完就一定存在的文件。这个 .ps1 随 resources\egress 打包，
#      powershell.exe 是系统自带的，两者都不依赖 venv 是否已经建好。
#
# 本文件是 UTF-8 **带 BOM**。Windows PowerShell 5.1 读无 BOM 的 UTF-8 会按 ACP 解，
# 中文注释会变成乱码；加 BOM 是唯一不依赖调用方指定 -Encoding 的写法。

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path      # <安装>\resources\egress
$Resources  = Split-Path -Parent $ScriptDir                        # <安装>\resources
$InstallDir = Split-Path -Parent $Resources                        # <安装>
$Backend    = Join-Path $Resources 'backend'

# 数据目录：只认安装时写下的那一格，其次才是默认。与 agent/egress/helper_client.py 的
# home_dir() 保持同一套规则——侧车与主进程必须算出**同一个** PLOBI_HOME，否则管道名不同、
# 口令文件不同，两边互相看不见却都觉得自己是好的。
$PlobiHome = (Get-ItemProperty -Path 'HKCU:\Software\Plobi' -Name DataDir -ErrorAction SilentlyContinue).DataDir
if ([string]::IsNullOrWhiteSpace($PlobiHome)) {
    $PlobiHome = Join-Path $env:LOCALAPPDATA 'Plobi'
}

$LogDir = Join-Path $PlobiHome 'logs'
try { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null } catch { }
$BootLog = Join-Path $LogDir 'egress-helper-boot.log'

function Write-Boot([string]$Message) {
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    try { Add-Content -Path $BootLog -Value $line -Encoding UTF8 } catch { }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Boot ("boot: admin={0} install={1} home={2}" -f (Test-Admin), $InstallDir, $PlobiHome)

if (-not (Test-Path $Backend)) {
    Write-Boot "boot: backend missing at $Backend — nothing to run"
    exit 1
}

# venv 就绪之前不该反复重试：这个任务每次登录只跑一次，第一次启动 Plobi 会自己把运行时
# 建好，下一次登录自然就成功了。等 90 秒是为了覆盖「开机后 Plobi 已经在建 venv」这一段。
$Python = $null
$deadline = (Get-Date).AddSeconds(90)
do {
    $candidate = Join-Path $PlobiHome 'plobi-agent\venv\Scripts\pythonw.exe'
    if (Test-Path $candidate) { $Python = $candidate; break }
    $candidate = Join-Path $PlobiHome 'plobi-agent\venv\Scripts\python.exe'
    if (Test-Path $candidate) { $Python = $candidate; break }
    Start-Sleep -Seconds 3
} while ((Get-Date) -lt $deadline)

if (-not $Python) {
    Write-Boot "boot: runtime not ready (checked $PlobiHome\plobi-agent\venv) — will try again next logon"
    exit 2
}

# wintun.dll 在 resources\egress\bin 里，引擎按这个位置找驱动；侧车与主进程共用同一份
# 判定，所以这里只把 PYTHONPATH 指到后端包根，其余交给 agent.egress 自己算。
$env:PYTHONPATH = $Backend
if (-not $env:PLOBI_HOME) { $env:PLOBI_HOME = $PlobiHome }

Write-Boot "boot: starting sidecar with $Python"
# --idle-exit：900 秒没人用就自己退。侧车是一个**握着管理员令牌**的常驻进程——让它为了
# 一次没再发生的点击一直挂着，是把权限借出去不还。用户真拨开关时主进程会 schtasks /Run
# 再把它叫起来（见 helper_client.start），冷启动代价是几百毫秒。
& $Python -m agent.egress.helper --home $PlobiHome --idle-exit 900 2>&1 | ForEach-Object {
    # 侧车自己会写 logs\egress-helper.log；这里只兜住"连 python 都没起来"的那种失败。
    Write-Boot ("helper: " + $_)
}
Write-Boot ("boot: sidecar exited with $LASTEXITCODE")
exit $LASTEXITCODE

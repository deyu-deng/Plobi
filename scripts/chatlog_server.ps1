<#
.SYNOPSIS
    Start chatlog HTTP server (headless).

.DESCRIPTION
    Launches the vendored chatlog server subcommand as a headless HTTP service
    listening on 127.0.0.1:5030 by default. Designed for Plobi data ingestion
    (MVP WeChat message collection).

    Keys are NEVER hardcoded: they are read from environment variables
    (CHATLOG_DATA_KEY / CHATLOG_IMG_KEY) unless explicitly passed via
    -DataKey / -ImgKey on the command line.

    Auto-decrypt is ON by default; disable with -NoAutoDecrypt.

.PARAMETER BinaryPath
    Path to the chatlog executable. Defaults to
    <repo>\tools\chatlog\bin\chatlog.exe; falls back to 'chatlog' on PATH.

.PARAMETER Addr
    Listen address, default 127.0.0.1:5030.

.PARAMETER DataDir
    WeChat data directory (chatlog --data-dir). Optional.

.PARAMETER DataKey
    WeChat data key. If omitted, read from env CHATLOG_DATA_KEY.

.PARAMETER ImgKey
    WeChat image key. If omitted, read from env CHATLOG_IMG_KEY.

.PARAMETER NoAutoDecrypt
    Switch OFF auto-decrypt (default is ON).

.PARAMETER ChatlogDebug
    Pass --debug to chatlog server. (改名了：原先这颗叫 -Debug，而脚本带
    [CmdletBinding()]——`-Debug` 是 PowerShell 的公共参数，同名声明会让这个脚本
    **一启动就报 MetadataError、根本跑不起来**。这台 10-16 10:5x 现核到的 sidecar
    "起不来"的第一层原因就在这颗参数上，不是密钥、不是 Go。)

.EXAMPLE
    .\chatlog_server.ps1

.EXAMPLE
    .\chatlog_server.ps1 -DataDir D:\WeChatData -BinaryPath D:\Tools\chatlog.exe

.EXAMPLE
    $env:CHATLOG_DATA_KEY = "xxx"; .\chatlog_server.ps1
#>
[CmdletBinding()]
param(
    [string]$BinaryPath = "",
    [string]$Addr = "127.0.0.1:5030",
    [string]$DataDir = "",
    [string]$DataKey = "",
    [string]$ImgKey = "",
    [switch]$NoAutoDecrypt,
    [switch]$ChatlogDebug
)

$ErrorActionPreference = "Stop"

# ---- Resolve binary path (configurable, with sensible default) ----
if ([string]::IsNullOrWhiteSpace($BinaryPath)) {
    # ⚠️ 这颗原来是 `Split-Path -Parent (Split-Path -Parent $PSScriptRoot)`——**多剥了一层**：
    # `$PSScriptRoot` 已经是 `<repo>\scripts` 这个**目录**（不是脚本文件），再往上一层就到
    # `Plobi-Desktop`，candidate 变成 `Plobi-Desktop\tools\chatlog\bin\chatlog.exe` ⇒ 永远"找不到产物"。
    # 这台 10-16 10:5x 现核：exe 在 `Code\tools\chatlog\bin\chatlog.exe`（29,475,840 B，10-10 22:10），
    # 而那句报错让 sidecar 起不来这件事被记成了"没产物"——**这是第二层死口，不是没装 Go、也不是没密钥**。
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $candidate = Join-Path $repoRoot "tools\chatlog\bin\chatlog.exe"
    if (Test-Path $candidate) {
        $BinaryPath = $candidate
    } else {
        $cmd = Get-Command chatlog -ErrorAction SilentlyContinue
        if ($cmd) {
            $BinaryPath = $cmd.Source
        } else {
            Write-Error "chatlog binary not found. Build it first: cd $repoRoot\tools\chatlog; go build -o bin\chatlog.exe ./cmd/chatlog ; or pass -BinaryPath <path>"
            exit 1
        }
    }
}
if (-not (Test-Path $BinaryPath)) {
    Write-Error "chatlog binary not found at: $BinaryPath"
    exit 1
}
Write-Host "[chatlog_server] binary: $BinaryPath"

# ---- Keys: 参数 > 环境变量 > 产品自己的 handoff，三档都不硬编 ----
# handoff 是采集侧写的那一份：`$PLOBI_HOME\chatlog\chatlog.json`
# （`plobi/collectors/chatlog/sidecar.py` 认的就是这一个）。以前这里只认参数与 env，
# 而没有人给 env ⇒ 引擎带着 `--auto-decrypt` 却没有 `--data-key` 起来，
# 自己退在 "dataKey is required"，面上看到的仍是"服务没在听"——**自愈拉一百次也是同一个死**。
$handoff = $null
$handoffPath = ""
$plobiHome = if ([string]::IsNullOrWhiteSpace($env:PLOBI_HOME)) {
    Join-Path $env:LOCALAPPDATA "plobi"
} else {
    $env:PLOBI_HOME
}
if (-not [string]::IsNullOrWhiteSpace($plobiHome)) {
    $handoffPath = Join-Path $plobiHome "chatlog\chatlog.json"
    if (Test-Path -PathType Leaf $handoffPath) {
        try {
            $handoff = Get-Content -LiteralPath $handoffPath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            Write-Host "[chatlog_server] handoff 读不动（$handoffPath）：$_" -ForegroundColor Yellow
        }
    }
}
if ([string]::IsNullOrWhiteSpace($DataKey) -and $env:CHATLOG_DATA_KEY) {
    $DataKey = $env:CHATLOG_DATA_KEY
}
if ([string]::IsNullOrWhiteSpace($ImgKey) -and $env:CHATLOG_IMG_KEY) {
    $ImgKey = $env:CHATLOG_IMG_KEY
}
if ($handoff) {
    if ([string]::IsNullOrWhiteSpace($DataKey))  { $DataKey  = [string]$handoff.data_key }
    if ([string]::IsNullOrWhiteSpace($ImgKey))   { $ImgKey   = [string]$handoff.img_key }
    if ([string]::IsNullOrWhiteSpace($DataDir))  { $DataDir  = [string]$handoff.data_dir }
    if ($Addr -eq "127.0.0.1:5030" -and $handoff.http_addr) { $Addr = [string]$handoff.http_addr }
}

# ---- 缺哪一件就说什么：不再让 Go 的英文替我们回答 ----
$missing = @()
if ([string]::IsNullOrWhiteSpace($DataDir)) { $missing += "微信数据目录（data_dir）" }
if ([string]::IsNullOrWhiteSpace($DataKey)) { $missing += "数据密钥（data_key）" }
if ($missing.Count -gt 0) {
    $where = if ([string]::IsNullOrWhiteSpace($handoffPath)) { "产品 handoff 文件不可用" } else { $handoffPath }
    Write-Error ("chatlog 起不来：还缺 " + ($missing -join "、") + "。它应当来自 " + $where +
                "，或由 -DataDir / -DataKey 参数、CHATLOG_DATA_KEY 环境变量给。" +
                "取钥匙/定位目录这两件不归这个脚本，见 Docs 的 WP-WECHAT-KEY-LOCATOR。")
    exit 2
}

# ---- Build argument list ----
$argsList = @("server")
$argsList += @("--addr", $Addr)
if (-not [string]::IsNullOrWhiteSpace($DataDir)) {
    $argsList += @("--data-dir", $DataDir)
}
if (-not [string]::IsNullOrWhiteSpace($DataKey)) {
    $argsList += @("--data-key", $DataKey)
}
if (-not [string]::IsNullOrWhiteSpace($ImgKey)) {
    $argsList += @("--img-key", $ImgKey)
}
if (-not $NoAutoDecrypt) {
    $argsList += "--auto-decrypt"
}
if ($ChatlogDebug) {
    $argsList += "--debug"
}

Write-Host "[chatlog_server] starting on $Addr (auto-decrypt=$(-not $NoAutoDecrypt)) ..."
Write-Host "[chatlog_server] command: & '$BinaryPath' $($argsList -join ' ')"

# ---- Launch (foreground; Ctrl+C to stop) ----
& $BinaryPath @argsList
exit $LASTEXITCODE

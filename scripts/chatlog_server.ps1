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

# ---- Keys: env-first, never hardcoded ----
if ([string]::IsNullOrWhiteSpace($DataKey)) {
    $DataKey = $env:CHATLOG_DATA_KEY
}
if ([string]::IsNullOrWhiteSpace($ImgKey)) {
    $ImgKey = $env:CHATLOG_IMG_KEY
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

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

.PARAMETER Debug
    Pass --debug to chatlog server.

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
    [switch]$Debug
)

$ErrorActionPreference = "Stop"

# ---- Resolve binary path (configurable, with sensible default) ----
if ([string]::IsNullOrWhiteSpace($BinaryPath)) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
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
if ($Debug) {
    $argsList += "--debug"
}

Write-Host "[chatlog_server] starting on $Addr (auto-decrypt=$(-not $NoAutoDecrypt)) ..."
Write-Host "[chatlog_server] command: & '$BinaryPath' $($argsList -join ' ')"

# ---- Launch (foreground; Ctrl+C to stop) ----
& $BinaryPath @argsList
exit $LASTEXITCODE

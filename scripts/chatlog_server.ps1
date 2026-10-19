<#
.SYNOPSIS
    Start chatlog HTTP server (headless).

.DESCRIPTION
    Launches the vendored chatlog server subcommand as a headless HTTP service
    listening on 127.0.0.1:5030 by default. Designed for Plobi data ingestion
    (MVP WeChat message collection).

    Keys are NEVER hardcoded and NEVER put on the command line: they are read
    from environment variables (CHATLOG_DATA_KEY / CHATLOG_IMG_KEY) or from the
    collector's own handoff ($PLOBI_HOME\chatlog\chatlog.json), then written into
    the engine's own config file ($PLOBI_HOME\chatlog\chatlog-server.json) with
    CHATLOG_DIR pinned to that directory. The exe is started with --addr only.

    Auto-decrypt is ON by default; disable with -NoAutoDecrypt.

.PARAMETER BinaryPath
    Path to the chatlog executable. Defaults to
    <repo>\plobi\collectors\chatlog\vendor\chatlog\bin\chatlog.exe;
    falls back to 'chatlog' on PATH.

.PARAMETER Addr
    Listen address, default 127.0.0.1:5030.

.PARAMETER DataDir
    WeChat data directory. Optional; defaults to the handoff's data_dir.

.PARAMETER DataKey
    WeChat data key. If omitted, read from env CHATLOG_DATA_KEY, then handoff.
    Goes into the engine config file, not into argv.

.PARAMETER ImgKey
    WeChat image key. If omitted, read from env CHATLOG_IMG_KEY, then handoff.

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
    # `Plobi-Desktop`，candidate 少一层 ⇒ 永远"找不到产物"。这台 10-16 10:5x 现核过一次。
    # 10-19 裁 187：整套 vendored 工程搬进微信那一族，构建落点跟着搬——**只这一处拼法**，
    # 而那句报错让 sidecar 起不来这件事被记成了"没产物"——**这是第二层死口，不是没装 Go、也不是没密钥**。
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $candidate = Join-Path $repoRoot "plobi\collectors\chatlog\vendor\chatlog\bin\chatlog.exe"
    if (Test-Path $candidate) {
        $BinaryPath = $candidate
    } else {
        $cmd = Get-Command chatlog -ErrorAction SilentlyContinue
        if ($cmd) {
            $BinaryPath = $cmd.Source
        } else {
            Write-Error "chatlog binary not found. Build it first: cd $repoRoot\plobi\collectors\chatlog\vendor\chatlog; go build -o bin\chatlog.exe . ; or pass -BinaryPath <path>"
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
$configDir = Join-Path $plobiHome "chatlog"
$serverConfigPath = Join-Path $configDir "chatlog-server.json"
if (-not [string]::IsNullOrWhiteSpace($plobiHome)) {
    $handoffPath = Join-Path $configDir "chatlog.json"
    if (Test-Path -PathType Leaf $handoffPath) {
        try {
            $handoff = Get-Content -LiteralPath $handoffPath -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            Write-Host "[chatlog_server] handoff 读不动（$handoffPath）：$_" -ForegroundColor Yellow
        }
    }
}

# 只报不可逆推的指纹前 8 位，与桌面那份 `keyFingerprint`（chatlog-keys.ts:59）同一把尺。
function Get-KeyFingerprint([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return "" }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($value))
    return ([System.BitConverter]::ToString($bytes) -replace '-', '').Substring(0, 8).ToLowerInvariant()
}

if ([string]::IsNullOrWhiteSpace($DataKey) -and $env:CHATLOG_DATA_KEY) {
    $DataKey = $env:CHATLOG_DATA_KEY
}
if ([string]::IsNullOrWhiteSpace($ImgKey) -and $env:CHATLOG_IMG_KEY) {
    $ImgKey = $env:CHATLOG_IMG_KEY
}
$Type = ""
$Platform = ""
$Version = 0
$FullVersion = ""
$WorkDir = ""
if ($handoff) {
    if ([string]::IsNullOrWhiteSpace($DataKey))  { $DataKey  = [string]$handoff.data_key }
    if ([string]::IsNullOrWhiteSpace($ImgKey))   { $ImgKey   = [string]$handoff.img_key }
    if ([string]::IsNullOrWhiteSpace($DataDir))  { $DataDir  = [string]$handoff.data_dir }
    if ($handoff.work_dir) { $WorkDir = [string]$handoff.work_dir }
    $Type = [string]$handoff.type
    $Platform = [string]$handoff.platform
    $FullVersion = [string]$handoff.full_version
    if ($handoff.version) { $Version = [int]$handoff.version }
    if ($Addr -eq "127.0.0.1:5030" -and $handoff.http_addr) { $Addr = [string]$handoff.http_addr }
}
if ([string]::IsNullOrWhiteSpace($WorkDir)) { $WorkDir = $configDir }
# 档位名要的是引擎那一侧的写法，不是 handoff 里的简称：`internal/wechat/model/process.go:16`
# 定的常量是 `windows`，而 `datasource.go:41-43` 那个 switch 拿 platform 逐字比——
# 抄 `win` 进去照样 503「unsupported platform:  v0」。
switch ($Platform.ToLowerInvariant()) {
    "win" { $Platform = "windows" }
    "mac" { $Platform = "darwin" }
    "osx" { $Platform = "darwin" }
}

# ---- 缺哪一件就说什么：不再让 Go 的英文替我们回答 ----
$missing = @()
if ([string]::IsNullOrWhiteSpace($DataDir)) { $missing += "微信数据目录（data_dir）" }
if ([string]::IsNullOrWhiteSpace($DataKey)) { $missing += "数据密钥（data_key）" }
if ([string]::IsNullOrWhiteSpace($Platform)) { $missing += "平台档位（platform）" }
if ($Version -le 0) { $missing += "版本档位（version，3 或 4）" }
if ($missing.Count -gt 0) {
    $where = if ([string]::IsNullOrWhiteSpace($handoffPath)) { "产品 handoff 文件不可用" } else { $handoffPath }
    Write-Error ("chatlog 起不来：还缺 " + ($missing -join "、") + "。它应当来自 " + $where +
                "，或由 -DataDir / -DataKey 参数、CHATLOG_DATA_KEY 环境变量给。" +
                "取钥匙/定位目录这两件不归这个脚本，见 Docs 的 WP-WECHAT-KEY-LOCATOR。")
    exit 2
}

# ---- 档位写进引擎配置，而不是留它去猜 ----
# vendor `internal/chatlog/conf/conf.go:74-75` 把 <data_dir>/chatlog.json 的兜底读法钉在
# `DataDir != "" && DataKey == ""` 这一档上：谁在 argv 给了 key，platform/version 这两档
# 就再也没人填 ⇒ `datasource.go:50` 报「unsupported platform:  v0」，HTTP 503。
# 这台 10-17 17:4x 的真读数就是这句（:5030 在听、/health 200，但 /api/v1/session 稳定 503）。
$serverConfig = [ordered]@{
    http_addr    = $Addr
    auto_decrypt = (-not $NoAutoDecrypt)
    work_dir     = $WorkDir
    data_dir     = $DataDir
    data_key     = $DataKey
}
if (-not [string]::IsNullOrWhiteSpace($Type))      { $serverConfig["type"] = $Type }
if (-not [string]::IsNullOrWhiteSpace($Platform))  { $serverConfig["platform"] = $Platform }
$serverConfig["version"] = [int]$Version
if (-not [string]::IsNullOrWhiteSpace($FullVersion)) { $serverConfig["full_version"] = $FullVersion }
if (-not [string]::IsNullOrWhiteSpace($ImgKey))    { $serverConfig["img_key"] = $ImgKey }

if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
}
# 无 BOM：viper 走 json.Unmarshal，PowerShell 5.1 的 `-Encoding utf8` 那个 BOM 会让引擎读不出配置。
[System.IO.File]::WriteAllText(
    $serverConfigPath,
    ($serverConfig | ConvertTo-Json -Depth 4),
    (New-Object System.Text.UTF8Encoding($false))
)

# 引擎自己的配置目录必须钉在 Plobi 家（D 盘）：`pkg/config/config.go:60-65` 在没人给
# CHATLOG_DIR 时回退 `os.UserHomeDir()\.chatlog` ⇒ 往 C 盘写运行数据（裁定 109 三）。
$env:CHATLOG_DIR = $configDir

# ---- Build argument list：密钥一律不上命令行 ----
# argv 里的 `--data-key` 会被这台任何进程用一句 `Get-CimInstance Win32_Process` 读走，
# 而 `conf.go:93` 那句 `log.Info().Msgf("server config: %s", ...)` 会把合并后的整包配置
# （含 data_key/img_key）打进 stdout ⇒ 桌面侧 `main.ts:9035` 早就是 stdio 全丢，这里照同一把尺做。
$argsList = @("server", "--addr", $Addr)
if ($ChatlogDebug) {
    $argsList += "--debug"
}

Write-Host "[chatlog_server] starting on $Addr (auto-decrypt=$(-not $NoAutoDecrypt)) ..."
Write-Host ("[chatlog_server] config: " + $serverConfigPath +
            "  data_key 指纹=" + (Get-KeyFingerprint $DataKey) +
            "  档位=" + $Platform + "/v" + $Version)

# ---- Launch (foreground; Ctrl+C to stop) ----
# 输出必须丢：`conf.go:93` 那句 `log.Info().Msgf("server config: %s", ...)` 打的是**合并后的整包配置**，
# 里面就有 data_key/img_key（桌面侧 `main.ts:9035` 早就把 stdio 全 ignore，同一个理由）。
# 但这里不能直接写 `2>&1`：脚本顶部是 `$ErrorActionPreference = "Stop"`，原生程序的 stderr 会被包成
# ErrorRecord 当场抛出——10-17 17:51 现核到 launcher 第一行日志就退（退出码 1，端口根本没听）。
# 所以这一档把 EAP 局部降回 Continue，跑完再恢复。
$savedPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $BinaryPath @argsList 2>&1 | Out-Null
$chatlogExit = $LASTEXITCODE
$ErrorActionPreference = $savedPreference
exit $chatlogExit

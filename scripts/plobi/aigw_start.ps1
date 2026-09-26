<#
.SYNOPSIS
    Start the local aigw (OpenAI-compatible desktop-quota gateway).

.DESCRIPTION
    Reads port from Code/aigw/config.yaml (do not assume 8000).
    If that port is already listening, print 已在跑 and probe /v1/models
    instead of starting a second copy. Pass -Recycle to stop that listener
    when its command line contains "aigw start", then start again so a
    config.yaml change is picked up. Keys stay in env / that yaml —
    this script never hardcodes production secrets.
#>
[CmdletBinding()]
param(
    [string]$Config = "",
    [switch]$Recycle
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$aigwDir = Join-Path $repoRoot "aigw"
if (-not $Config) {
    $Config = Join-Path $aigwDir "config.yaml"
}
if (-not (Test-Path $Config)) {
    Write-Error "aigw config not found: $Config"
    exit 1
}

function Get-AigwPort {
    param([string]$ConfigPath)
    $inServer = $false
    foreach ($raw in Get-Content -Path $ConfigPath -Encoding UTF8) {
        $line = ($raw -split "#", 2)[0].TrimEnd()
        if ($line -match '^server:\s*$') {
            $inServer = $true
            continue
        }
        if ($inServer -and $line -match '^\S') {
            $inServer = $false
        }
        if ($inServer -and $line -match '^\s+port:\s*(\d+)\s*$') {
            return [int]$Matches[1]
        }
    }
    return 8000
}

function Test-LocalPortListening {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(400)
        if (-not $ok) {
            return $false
        }
        $client.EndConnect($iar) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortListenerPids {
    param([int]$Port)
    $ids = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($c in @($conns)) {
            if ($c -and $c.OwningProcess) {
                $ids += [int]$c.OwningProcess
            }
        }
    } catch {
        return @()
    }
    return @($ids | Select-Object -Unique)
}

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if ($proc) {
        return [string]$proc.CommandLine
    }
    return ""
}

function Stop-AigwListeners {
    param([int]$Port)
    $listenerPids = @(Get-PortListenerPids -Port $Port)
    if ($listenerPids.Count -eq 0) {
        Write-Host "Recycle: :$Port not listening"
        return
    }

    $stopParent = New-Object System.Collections.Generic.List[int]
    $stopChild = New-Object System.Collections.Generic.List[int]
    foreach ($listenerId in $listenerPids) {
        $cmd = Get-ProcessCommandLine -ProcessId $listenerId
        if (-not $cmd -or ($cmd -notlike "*aigw start*")) {
            Write-Error "Recycle refused: PID $listenerId on :$Port is not an aigw start process. cmd=$cmd"
            exit 1
        }
        $stopChild.Add($listenerId)
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$listenerId" -ErrorAction SilentlyContinue
        if ($proc -and $proc.ParentProcessId) {
            $parentId = [int]$proc.ParentProcessId
            $parentCmd = Get-ProcessCommandLine -ProcessId $parentId
            if ($parentCmd -and ($parentCmd -like "*aigw start*")) {
                $stopParent.Add($parentId)
            }
        }
    }

    $seen = @{}
    foreach ($stopId in @($stopParent + $stopChild)) {
        if ($seen.ContainsKey($stopId)) { continue }
        $seen[$stopId] = $true
        Write-Host "Recycle: stopping PID $stopId (cmdline matches aigw start)"
        Stop-Process -Id $stopId -Force -ErrorAction SilentlyContinue
    }

    $deadline = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-LocalPortListening -Port $Port)) { break }
        Start-Sleep -Milliseconds 200
    }
    if (Test-LocalPortListening -Port $Port) {
        Write-Error "Recycle: :$Port still listening after stop"
        exit 1
    }
    Write-Host "Recycle: :$Port is free"
}

function Show-AigwModels {
    param([int]$Port)
    $url = "http://127.0.0.1:${Port}/v1/models"
    $headers = @{ Authorization = "Bearer sk-local-dev-key" }
    try {
        $resp = Invoke-WebRequest -Uri $url -Headers $headers -UseBasicParsing -TimeoutSec 3
        Write-Host "GET $url HTTP $($resp.StatusCode)"
        $body = [string]$resp.Content
        if ($body.Length -gt 240) {
            $body = $body.Substring(0, 240) + "..."
        }
        Write-Host $body
    } catch {
        Write-Host "GET $url failed: $($_.Exception.Message)"
    }
}

$port = Get-AigwPort -ConfigPath $Config
if ($Recycle -and (Test-LocalPortListening -Port $port)) {
    Stop-AigwListeners -Port $port
}
if (Test-LocalPortListening -Port $port) {
    Write-Host "已在跑 :$port — skip start, probing /v1/models"
    Show-AigwModels -Port $port
    exit 0
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }
Set-Location $aigwDir
Write-Host "Starting aigw with $Config using $python (port $port)"
& $python -m aigw start --config $Config

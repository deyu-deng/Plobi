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

    Before the health probe the script reconciles the key it is about to
    send against the key the gateway itself declares (WP-AIGW-KEY-LITERALS,
    裁定 81 + 待决 #29 "consumer may keep a fallback, but it must be
    reconciled at runtime and go red on disagreement").  The declaration is
    read through plobi/agents/registry.py — the reader the Plobi side already
    settled on — never by a second parser written here, so the two cannot
    drift over "how to parse this file".  Only source labels and SHA-256
    prefixes reach the console; the key value stays in memory.  A gateway
    declaration that cannot be read is reported as NOT_CHECKED and is never
    treated as a match.  Pass -KeyCheck to run the reconciliation alone,
    without touching the port, and exit 0 only on MATCH.
#>
[CmdletBinding()]
param(
    [string]$Config = "",
    [switch]$Recycle,
    [switch]$KeyCheck
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

function Resolve-AigwPython {
    param([string]$RepoRoot)
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    return "python"
}

function Get-AigwKeyReconciliation {
    <#
    .SYNOPSIS
        Ask the gateway's own declaration what key it accepts, and compare it
        with the key this script would send.
    .DESCRIPTION
        Returns Verdict / UsedSource / UsedFp / DeclaredFp / Key / Marker /
        Detail.  Verdict is MATCH, MISMATCH or NOT_CHECKED.  Anything that
        keeps us from reading the declaration -- missing interpreter, failing
        import, unreadable config, a config that declares no server.api_key --
        is NOT_CHECKED, never an assumed match.  Key is populated only on
        MATCH and is only ever used for the Authorization header: no verdict
        text carries the value.
    #>
    param(
        [string]$ConfigPath,
        [string]$PythonExe,
        [string]$RepoRoot
    )

    # Deliberately NOT a PowerShell parser for server.api_key.  The Plobi side
    # already owns one narrow reader (plobi/agents/registry.py
    # ::gateway_declared_api_key) and the desktop shell mirrors its semantics;
    # a third reading of the same file is the drift this work package exists to
    # stop.  The snippet stays on one line and uses only single quotes so that
    # Windows PowerShell 5.1 can pass it as a single native argument without
    # mangling embedded quotes.
    $snippet = "import sys,json,hashlib;sys.path.insert(0,sys.argv[1]);from pathlib import Path;from plobi.agents.registry import gateway_declared_api_key,aigw_api_key,aigw_credential_source;declared=gateway_declared_api_key(Path(sys.argv[2]));used=aigw_api_key();source=aigw_credential_source();fp=(lambda v:hashlib.sha256(v.encode('utf-8')).hexdigest()[:12]);ok=bool(declared) and used==declared;print(json.dumps({'verdict':('NOT_CHECKED' if not declared else ('MATCH' if ok else 'MISMATCH')),'used_source':source,'used_fp':fp(used),'declared_fp':(fp(declared) if declared else None),'key':(used if ok else None)}))"

    $reason = ""
    $payload = $null
    try {
        if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
            throw "python interpreter not found: $PythonExe"
        }
        $raw = & $PythonExe -c $snippet $RepoRoot $ConfigPath
        if ($null -eq $LASTEXITCODE) {
            # Windows PowerShell spawns nothing when PATHEXT is absent from the
            # environment and reports no exit code at all.  A reader that never
            # ran is a read failure, so it lands in NOT_CHECKED rather than in
            # an assumed match.
            throw "gateway declaration reader did not run (no exit code from $PythonExe)"
        }
        if ($LASTEXITCODE -ne 0) {
            throw "gateway declaration reader exited $LASTEXITCODE"
        }
        $line = @($raw | Where-Object { "$_" -match '^\{' } | Select-Object -Last 1)
        if ($line.Count -eq 0) {
            throw "gateway declaration reader printed no verdict"
        }
        $payload = $line[0] | ConvertFrom-Json
    } catch {
        $reason = [string]$_.Exception.Message
    }

    if (-not $payload) {
        return [pscustomobject]@{
            Verdict     = "NOT_CHECKED"
            UsedSource  = "unresolved"
            UsedFp      = "unresolved"
            DeclaredFp  = $null
            Key         = $null
            Marker      = "AIGW_KEY_VERDICT=NOT_CHECKED gateway_config=$ConfigPath reason=$reason"
            Detail      = "aigw key NOT CHECKED: could not read the gateway's declared server.api_key from $ConfigPath ($reason). Not probing with a key nobody declared, and not assuming this script's key matches."
        }
    }

    $usedSource = [string]$payload.used_source
    $usedFp = [string]$payload.used_fp
    $declaredFp = if ($payload.declared_fp) { [string]$payload.declared_fp } else { $null }
    $key = if ($payload.key) { [string]$payload.key } else { $null }

    switch ($payload.verdict) {
        "MATCH" {
            return [pscustomobject]@{
                Verdict    = "MATCH"
                UsedSource = $usedSource
                UsedFp     = $usedFp
                DeclaredFp = $declaredFp
                Key        = $key
                Marker     = "AIGW_KEY_VERDICT=MATCH script_source=$usedSource script_fp=$usedFp gateway_fp=$declaredFp gateway_config=$ConfigPath"
                Detail     = "aigw key ok: this script sends $usedSource fp=$usedFp and the gateway declares fp=$declaredFp in $ConfigPath"
            }
        }
        "MISMATCH" {
            return [pscustomobject]@{
                Verdict    = "MISMATCH"
                UsedSource = $usedSource
                UsedFp     = $usedFp
                DeclaredFp = $declaredFp
                Key        = $null
                Marker     = "AIGW_KEY_VERDICT=MISMATCH script_source=$usedSource script_fp=$usedFp gateway_fp=$declaredFp gateway_config=$ConfigPath"
                Detail     = "aigw key MISMATCH: the gateway uses server.api_key in $ConfigPath (fp=$declaredFp) but this script uses $usedSource (fp=$usedFp). Refusing to probe: one of the two is stale, and the 401 you would get reads like a dead key."
            }
        }
        default {
            return [pscustomobject]@{
                Verdict    = "NOT_CHECKED"
                UsedSource = $usedSource
                UsedFp     = $usedFp
                DeclaredFp = $null
                Key        = $null
                Marker     = "AIGW_KEY_VERDICT=NOT_CHECKED script_source=$usedSource script_fp=$usedFp gateway_config=$ConfigPath reason=no readable server.api_key"
                Detail     = "aigw key NOT CHECKED: $ConfigPath declares no server.api_key this script can read (it uses $usedSource fp=$usedFp). Not treating 'cannot tell' as 'agrees', and not probing with a hardcoded fallback."
            }
        }
    }
}

function Show-AigwModels {
    param(
        [int]$Port,
        [string]$Key
    )
    $url = "http://127.0.0.1:${Port}/v1/models"
    $headers = @{ Authorization = "Bearer $Key" }
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

$python = Resolve-AigwPython -RepoRoot $repoRoot

if ($KeyCheck) {
    $check = Get-AigwKeyReconciliation -ConfigPath $Config -PythonExe $python -RepoRoot $repoRoot
    Write-Host $check.Marker
    Write-Host $check.Detail
    if ($check.Verdict -ne "MATCH") {
        Write-Error "aigw key check failed: $($check.Verdict)"
        exit 1
    }
    exit 0
}

$port = Get-AigwPort -ConfigPath $Config
if ($Recycle -and (Test-LocalPortListening -Port $port)) {
    Stop-AigwListeners -Port $port
}
if (Test-LocalPortListening -Port $port) {
    Write-Host "已在跑 :$port — skip start, probing /v1/models"
    # Reconcile before probing: the probe's whole diagnostic value is that it
    # authenticates with the key the gateway actually accepts.
    $check = Get-AigwKeyReconciliation -ConfigPath $Config -PythonExe $python -RepoRoot $repoRoot
    Write-Host $check.Marker
    Write-Host $check.Detail
    if ($check.Verdict -ne "MATCH") {
        Write-Error "aigw health check refused: the key this script would send ($($check.Verdict)) is not the key the gateway declares in $Config"
        exit 1
    }
    Show-AigwModels -Port $port -Key $check.Key
    exit 0
}

Set-Location $aigwDir
Write-Host "Starting aigw with $Config using $python (port $port)"
& $python -m aigw start --config $Config

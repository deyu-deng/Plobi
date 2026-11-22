# 注册那只「提权手」的计划任务。由 installer.nsh 在装完时调用一次。
#
# 为什么这段逻辑是脚本文件，而不是 NSIS 里拼命令行（2026-11-22 实测）：
#   ① `schtasks /Create ... /TR "powershell.exe -File \"C:\...\x.ps1\""` **一定失败**——
#      schtasks 的 /TR 不认内嵌引号，它按空格切参数，把 `Files\Plobi\...` 当成非法选项
#      （实测报 "Invalid argument/option - 'Files\Plobi\resources\egress\egress-helper.ps1'"）。
#      而 Program Files 这类路径**必须**带引号。
#   ② 换 /XML 也不通：schtasks 只吃 UTF-16 的 XML，NSIS 的 FileWrite 只会写单字节，
#      实测报 "The task XML is malformed"。
#   ③ PowerShell 写 Unicode 文件、调 schtasks 都是它本行，还能顺手把路径里的 XML 元字符
#      转义掉（安装目录里带 & 的人确实存在）。所以这层壳交给 PowerShell，NSIS 只负责叫它。
#
# 参数：-InstallDir 安装根目录（installer.nsh 传 $INSTDIR）；-TaskName 便于测试时换名。
# 退出码：0 成功；非 0 = schtasks 的失败码，同时记进 HKCU\Software\Plobi\EgressHelperTaskError。

param(
    [string]$InstallDir = "",
    [string]$TaskName = 'Plobi Egress Helper'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    # 兜底：本文件在 <安装>\resources\egress\ 下，往上三层就是安装根。
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $InstallDir = Split-Path -Parent (Split-Path -Parent $here)
}

$helper = Join-Path $InstallDir 'resources\egress\egress-helper.ps1'
$escaped = [System.Security.SecurityElement]::Escape($helper)
$user = "$env:USERDOMAIN\$env:USERNAME"

# LogonTrigger + HighestAvailable：登录时以最高权限静默跑，不弹 UAC。
# IgnoreNew：已经在跑就别再起第二个（侧车的管道是独占的，第二个会自己退，但让任务层
# 直接挡掉更干净）。
# ExecutionTimeLimit=PT0S：不设时限——侧车要一直活着直到空闲自退。
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Plobi egress helper: 以最高权限运行出口代理引擎（TUN / 系统代理）。卸载 Plobi 时会被摘除。</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <!-- 只认这个账户登录：不写 UserId 就变成「任何用户登录都拉起」，在多账户机器上等于
           给别人的登录也塞一只本用户的提权进程（会因账户不匹配起不来，但日志里全是噪音）。 -->
      <Enabled>true</Enabled>
      <UserId>$user</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$user</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$escaped"</Arguments>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = Join-Path $env:TEMP 'plobi-egress-helper-task.xml'
# UTF-16 + BOM：schtasks 只认这个（BOM 不能省，实测没有 BOM 的 UTF-16 会被当成畸形 XML）。
[System.IO.File]::WriteAllText($xmlPath, $xml, (New-Object System.Text.UnicodeEncoding($false, $true)))

try {
    & schtasks.exe /Create /TN $TaskName /XML $xmlPath /F | Out-Null
    $rc = $LASTEXITCODE
} finally {
    Remove-Item $xmlPath -Force -ErrorAction SilentlyContinue
}

$regPath = 'HKCU:\Software\Plobi'
if ($rc -ne 0) {
    # 注册失败不打断安装（非管理员账户装 Plobi 是合法的，只是没有 TUN）。把码留下来，
    # 出问题时能查到是「哪一次、什么码」，而不是一句「就是不行」。
    New-Item -Path $regPath -Force | Out-Null
    New-ItemProperty -Path $regPath -Name 'EgressHelperTaskError' -Value "$rc" -PropertyType String -Force | Out-Null
    exit $rc
}

Remove-ItemProperty -Path $regPath -Name 'EgressHelperTaskError' -ErrorAction SilentlyContinue
exit 0

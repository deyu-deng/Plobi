# 摘掉提权手。由 installer.nsh 在卸载时调用。
#
# 两件事都要做：
#   ① 删掉计划任务——程序卸了还留一个「能以最高权限被唤醒」的入口，比残留文件严重得多。
#   ② 收掉可能还在跑的侧车。任务删了进程不会跟着走：它是个提着管理员令牌的常驻进程
#      （空闲 15 分钟自退，但正好在用网时就还在）。按命令行精确匹配，不按进程名杀 python
#      ——那会把用户别的 python 一起杀了。

param(
    [string]$TaskName = 'Plobi Egress Helper'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

& schtasks.exe /Delete /TN $TaskName /F | Out-Null

try {
    Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object { $_.CommandLine -and $_.CommandLine -like '*agent.egress.helper*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {
    # 拿不到进程列表（极少见）不该让卸载卡住——任务已经删了，进程会因空闲自退。
}

Remove-ItemProperty -Path 'HKCU:\Software\Plobi' -Name 'EgressHelperTaskError' -ErrorAction SilentlyContinue

exit 0

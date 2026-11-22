; WP-DATA-ISOLATION — 安装时选「数据目录」。
;
; 装机版只认这一格（HKCU\Software\Plobi\DataDir），其次才是 %LOCALAPPDATA%\Plobi。
; 数据目录与安装目录是两件事：程序放 D 盘、数据放另一块盘的人很多。
;
; 三个形状上的坑（都踩过）：
;   ① 自定义页的正文在 Function 里——顶层用 MUI_HEADER_TEXT 会展开成 SendMessage，非法。
;   ② MUI2 自己先 include：electron-builder 把用户脚本排在模板前面，编译期宏还没定义。
;   ③ 本文件带 UTF-8 BOM——NSIS 按 ACP 读，没 BOM 的中文会乱码。

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"

Var PlobiDataPage
Var PlobiDataDirField
Var PlobiDataDirValue

; 默认格：上一次装过就沿用上次那一格，否则是 %LOCALAPPDATA%\Plobi。
;
; 这一格不只是页面上那个默认值——**静默更新（/S）时那一页根本不跑**，那一格只能从这里
; 来；否则每次更新都会把用户选的位置改回默认格。
!macro preInit
  StrCpy $PlobiDataDirValue "$LOCALAPPDATA\Plobi"
  ReadRegStr $0 HKCU "Software\Plobi" "DataDir"
  ${If} $0 != ""
    StrCpy $PlobiDataDirValue $0
  ${EndIf}
!macroend

!macro customPageAfterChangeDir
  Page custom PlobiDataPageFunc PlobiDataPageLeave
!macroend

Function PlobiDataPageFunc
  !insertmacro MUI_HEADER_TEXT "选择数据目录" "Plobi 的会话、配置与记忆存放在这里。"

  nsDialogs::Create 1018
  Pop $PlobiDataPage

  ${If} $PlobiDataPage == error
    Abort
  ${EndIf}

  ${NSD_CreateDirRequest} 0 32u 78% 14u "$PlobiDataDirValue"
  Pop $PlobiDataDirField

  ${NSD_CreateBrowseButton} 80% 32u 20% 14u "浏览…"
  Pop $0
  ${NSD_OnClick} $0 plobiDataBrowse

  ${NSD_CreateLabel} 0 54u 100% 20u "卸载时不会删除这个文件夹里的内容。"
  Pop $0

  nsDialogs::Show
FunctionEnd

; 值在这里读，不在上面的页面函数里读。
;
; 为什么（2026-11-07 实测）：nsDialogs 的控件**只在 `nsDialogs::Show` 之前活着**——
; Show 返回时对话框连同控件已经销毁 ⇒ 那时再去读句柄只会读回空（用户明明选了 D 盘，
; 记下来的却是默认格）。官方形状是 `Page custom 创建函数 离开函数`：离开回调在用户
; 点「下一步」时跑，控件那时还在。
Function PlobiDataPageLeave
  ${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue
FunctionEnd

Function plobiDataBrowse
  ${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue
  nsDialogs::SelectFolderDialog "选择 Plobi 数据目录" "$PlobiDataDirValue"
  Pop $0
  ${If} $0 != error
    ${NSD_SetText} $PlobiDataDirField "$0"
  ${EndIf}
FunctionEnd

; ── 提权侧车（方案 B，2026-11-22 定）────────────────────────────────────────────
;
; TUN 要建网卡就必须有一个管理员进程，这是 Windows 的硬约束。三条路里：
;   A 整包提权（electron-builder 的 perMachine）—— 连安装都要管理员，且主进程全程握着令牌；
;   C exe 设 requireAdministrator —— 每次开 Plobi 弹一次 UAC，用户当场否掉；
;   B 侧车 —— 主进程保持普通权限，另有一个只干「起引擎、建网卡、写系统代理」的提权进程。
; 选 B。它靠的就是这个计划任务：ONLOGON 触发 + /RL HIGHEST，登录时静默拿到最高权限，
; 全程不弹 UAC —— 这是 Windows 允许「以管理员身份跑、还不打扰用户」的唯一通道。
;
; 任务动作是 .ps1 而不是 python.exe，理由写在那个脚本开头（装完时 venv 还不存在；数据目录
; 是用户选的，安装器算不出来）。这里只负责注册与摘除。
;
; 注册失败**不许**打断安装：非管理员账户装 Plobi 是合法的，只是没有 TUN —— 安装器不该
; 因为一只可选的提权手而回滚整次安装。失败码记在注册表里，诊断时能查到那一次到底怎么了。

!define PLOBI_HELPER_TASK "Plobi Egress Helper"

!macro PlobiEgressHelperRegister
  ; 注册逻辑放在 register-helper-task.ps1 里，因为 NSIS 里两条路都实测不通：
  ;   ① `schtasks /Create ... /TR "powershell.exe -File \"C:\Program Files\...\x.ps1\""` ——
  ;      /TR 不接受内嵌引号，实测报 Invalid argument/option - 'Files\Plobi\...'；
  ;      而 Program Files 这种安装路径**必须**带引号。
  ;   ② 改 /XML —— schtasks 只认 UTF-16 的 XML，NSIS 的 FileWrite 只会写单字节，
  ;      实测报 The task XML is malformed。
  ; ExecWait 走 CreateProcess，命令行里的引号是字面量、不经 schtasks 那层参数解析，所以
  ; 这里把带引号的路径交给 PowerShell 是安全的。
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $\"$INSTDIR\resources\egress\register-helper-task.ps1$\" -InstallDir $\"$INSTDIR$\"'
  ; 失败不打断安装：脚本自己会把失败码写进 EgressHelperTaskError。非管理员账户装 Plobi
  ; 是合法的，只是没有 TUN —— 安装器不该因为一只可选的提权手回滚整次安装。
!macroend

!macro PlobiEgressHelperRemove
  ${If} ${FileExists} "$INSTDIR\resources\egress\unregister-helper-task.ps1"
    ; 脚本还在：让它一并收掉残留的提权进程（按命令行匹配，不按进程名杀 python）。
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $\"$INSTDIR\resources\egress\unregister-helper-task.ps1$\"'
  ${Else}
    ; 脚本已被删（正常顺序下不会）：至少把任务摘掉，不留一条随时能提权的入口。
    ExecWait 'schtasks.exe /Delete /TN "${PLOBI_HELPER_TASK}" /F'
  ${EndIf}
  DeleteRegValue HKCU "Software\Plobi" "EgressHelperTaskError"
!macroend

; 装的时候把那一格写进注册表（主进程 electron/main.ts 的 readInstallerRecordedDataDir 读它）。
!macro customInstall
  ${If} $PlobiDataDirValue == ""
    StrCpy $PlobiDataDirValue "$LOCALAPPDATA\Plobi"
  ${EndIf}

  ; 去掉手打时容易带上的尾分隔符，注册表里留一条干净的绝对路径。
  ${If} $PlobiDataDirValue != ""
    StrCpy $0 $PlobiDataDirValue 1 -1
    ${If} $0 == "\"
      StrCpy $PlobiDataDirValue $PlobiDataDirValue -1
    ${EndIf}
  ${EndIf}

  CreateDirectory "$PlobiDataDirValue"
  WriteRegStr HKCU "Software\Plobi" "DataDir" "$PlobiDataDirValue"

  ; 通知的「点一下」是 Windows 按 AUMID 唤起「登记在这个 AUMID 上的可执行文件」。
  ; 这一格得登记（electron-builder 的卸载器本来就按这个键清理，我们此前却没登记
  ; ⇒ 系统只能靠开始菜单快捷方式去猜，点通知可能唤起别的东西）。
  ; 与 package.json 的 build.appId 一致。
  WriteRegStr HKCU "Software\Classes\AppUserModelId\com.plobi.desktop" "" "Plobi"

  ; 提权侧车跟着装。静默更新（/S）也会走到这里——那条路上 $INSTDIR 由 electron-builder
  ; 给，不经过自定义页，所以这条路也是同一个宏、同一份路径。
  !insertmacro PlobiEgressHelperRegister
!macroend

!macro customUnInstall
  ; 只摘程序的门牌，不动数据，也不抹掉「数据目录在哪一格」——重装指回同一格，历史还在。
  ; 这句话原先是靠 `DeleteRegKey /ifempty HKCU "Software\Plobi"` 完成的，但 /ifempty 只看
  ; 子键：DataDir 是值不是子键，于是那一行照样会把路径记忆删掉，卸载后重装默认回到
  ; %LOCALAPPDATA%\Plobi，D 盘那份库看着就像丢了。别删这个键。
  DeleteRegKey HKCU "Software\Classes\AppUserModelId\com.plobi.desktop"

  ; 摘掉那只提权手。程序都卸了还留一个能以管理员身份被唤醒的计划任务，是比残留文件严重
  ; 得多的东西——它是一条随时能提权的入口。
  !insertmacro PlobiEgressHelperRemove
!macroend

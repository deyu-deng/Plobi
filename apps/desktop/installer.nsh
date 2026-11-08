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
!macroend

!macro customUnInstall
  ; 只摘程序的门牌，不动数据，也不抹掉「数据目录在哪一格」——重装指回同一格，历史还在。
  DeleteRegKey /ifempty HKCU "Software\Plobi"
  DeleteRegKey HKCU "Software\Classes\AppUserModelId\com.plobi.desktop"
!macroend

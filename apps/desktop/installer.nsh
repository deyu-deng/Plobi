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

; 安装器一开跑就把默认格摆好；用户不动它就是一个干净的新装。
!macro preInit
  StrCpy $PlobiDataDirValue "$LOCALAPPDATA\Plobi"
!macroend

!macro customPageAfterChangeDir
  Page custom PlobiDataPageFunc
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

  ${NSD_CreateLabel} 0 54u 100% 20u "默认位置：%LOCALAPPDATA%\Plobi"
  Pop $0

  ${NSD_CreateLabel} 0 70u 100% 20u "卸载时不会删除这个文件夹里的内容。"
  Pop $0

  nsDialogs::Show

  ; 值在这里读进 Var：customInstall 跑在安装 Section 里，那时页面早销毁了，
  ; 再去读那个控件句柄只会读回空（落回默认格）。
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
!macroend

!macro customUnInstall
  ; 只摘门牌，不动数据。卸载后重装指回同一格，历史还在。
  DeleteRegValue HKCU "Software\Plobi" "DataDir"
  DeleteRegKey /ifempty HKCU "Software\Plobi"
!macroend

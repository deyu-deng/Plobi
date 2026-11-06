; 这一份 2026-11-06 从 `apps/desktop/build/installer.nsh` 挪到这里来。
;
; 为什么挪：`apps/desktop/build/` 整个目录是 **gitignore** 的（`.gitignore:76`）⇒ 这份脚本
; 原来只活在某一台机器的本地，**CI 的干净 checkout 里根本没有它** ⇒ 「装的时候能自选数据
; 目录」那一页进不了任何一个发布包（而 `package.json` 里指路它的那行若签上去，CI 构建
; 会因为找不到 include 而直接断——两件事是同一根绳子）。
;
; electron-builder 的 `nsis.include` 按**工程目录**（`apps/desktop/`）解析
; （`NsisTarget.js:600` 的 `packager.getResource(this.options.include, ...)`）⇒ 指到这一份。
;
; WP-DATA-ISOLATION — 安装时可选择「数据目录」。
;
; 为什么需要这一页：装机版从今往后**不看** PLOBI_HOME（那是开发态的门牌），
; 它只认这里写下的一格，其次才是自己的默认格 %LOCALAPPDATA%\Plobi。
; 于是同一台机器上「稳定的装机版」与「天天改的开发态」各占一格，谁也污染不到谁。
;
; 写下的东西只有一处：HKCU\Software\Plobi 的 DataDir
; （主进程 apps/desktop/electron/main.ts 的 readInstallerRecordedDataDir 读它）。
;
; 用户把这一格指到一个空目录，就是一次完整体验新用户首次打开：后端 bootstrap
; 会在 <数据目录> 下从零长出 plobi-agent，没有任何历史会话、名册行、记忆。

!include "nsDialogs.nsh"
!include "LogicLib.nsh"

Var PlobiDataPage
Var PlobiDataDirField
Var PlobiDataDirValue

; 安装器一开跑就把默认格摆好；用户不动它就是一个干净的新装。
!macro preInit
  StrCpy $PlobiDataDirValue "$LOCALAPPDATA\Plobi"
!macroend

; 紧跟在「选择安装目录」之后插一页。安装目录与数据目录是两件事：程序放 D 盘、
; 数据放另一块盘的人很多，混在一页会让人以为改一个就改了另一个。
!macro customPageAfterChangeDir
  !insertmacro MUI_HEADER_TEXT "选择数据目录" "Plobi 把会话、配置与记忆放在这里。"

  nsDialogs::Create 1018
  Pop $PlobiDataPage

  ${If} $PlobiDataPage == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 26u "选择一个用来存放 Plobi 数据的文件夹（会话记录、Agent 名册、模型与技能配置、日志）。$\r$\n指向一个空文件夹即可体验全新用户的第一次启动流程；已有的数据目录请原样指回去。"
  Pop $0

  ${NSD_CreateDirRequest} 0 32u 78% 14u "$PlobiDataDirValue"
  Pop $PlobiDataDirField

  ${NSD_CreateBrowseButton} 80% 32u 20% 14u "浏览..."
  Pop $0
  ${NSD_OnClick} $0 plobiDataBrowse

  ${NSD_CreateLabel} 0 54u 100% 20u "默认：%LOCALAPPDATA%\Plobi（当前用户）。卸载时不会删除这个文件夹里的内容。"
  Pop $0

  nsDialogs::Show
!macroend

Function plobiDataBrowse
  ${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue
  nsDialogs::SelectFolderDialog "选择 Plobi 数据目录" "$PlobiDataDirValue"
  Pop $0
  ${If} $0 != error
    ${NSD_SetText} $PlobiDataDirField "$0"
  ${EndIf}
FunctionEnd

!macro customInstall
  ${If} $PlobiDataDirField != 0
    ${NSD_GetText} $PlobiDataDirField $PlobiDataDirValue
  ${EndIf}

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

; MangaFlow WPF 桌面客户端安装器（NUI-8 P4-1）
;
; 布局契约：客户端 App.xaml.cs:41-47 在无 MANGAFLOW_NATIVE_REPO 时，从自身目录向上
; 找 apps\api\alembic.ini 当作 repository；NativeBackend 再据此定位 sidecar 与
; .venv-desktop。因此安装根目录必须自带 apps\api、apps\desktop\sidecar、.venv-desktop，
; 客户端 exe 放在其下的 client\。这样零改生产代码即可从安装目录启动。
;
; 逐用户安装（RequestExecutionLevel user）：不写机器级注册表、不要管理员。
; 卸载只清除安装目录，绝不动 %LOCALAPPDATA%\MangaFlow\Native 与旧 Tauri 壳的
; %LOCALAPPDATA%\com.mangaflow.desktop（用户数据与另一套数据目录并存是 #410 红线）。

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef STAGING
  !error "STAGING 未定义：请先用 build-native-installer.ps1 生成 staging 目录"
!endif
!ifndef OUTFILE
  !define OUTFILE "MangaFlow-Native-${VERSION}-x64.exe"
!endif
; 分发用 lzma 压缩（200MB 级 payload 首包很慢），CI/本地验证用 zlib 保证可完成。
!ifndef COMPRESSOR
  !define COMPRESSOR "zlib"
!endif

Name "MangaFlow 桌面客户端"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\MangaFlow"
InstallDirRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MangaFlow.Native" "InstallLocation"
RequestExecutionLevel user
SetCompressor /SOLID ${COMPRESSOR}
SetCompress auto
SetOverwrite try
Unicode true
XPStyle on

!define APP_UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MangaFlow.Native"

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "MangaFlow 桌面客户端" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR\client"
  File /r "${STAGING}\client\*.*"

  SetOutPath "$INSTDIR\apps\api"
  File /r "${STAGING}\apps\api\*.*"

  SetOutPath "$INSTDIR\apps\desktop\sidecar"
  File /r "${STAGING}\apps\desktop\sidecar\*.*"

  SetOutPath "$INSTDIR\.venv-desktop"
  File /r "${STAGING}\.venv-desktop\*.*"

  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "Displayname" "MangaFlow 桌面客户端"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "Publisher" "MangaFlow"
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${APP_UNINSTALL_KEY}" "NoRepair" 1
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\unins000.exe" /S'
  WriteRegStr HKCU "${APP_UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\unins000.exe" /S'

  ; NO_SHORTCUTS：安装/卸载验收必须在临时目录里跑，不能往真实桌面写快捷方式。
  !ifndef NO_SHORTCUTS
    CreateShortCut "$DESKTOP\MangaFlow.lnk" "$INSTDIR\client\MangaFlow.Native.exe" "" "$INSTDIR\client\MangaFlow.Native.exe" 0
    CreateShortCut "$SMPROGRAMS\MangaFlow\MangaFlow.lnk" "$INSTDIR\client\MangaFlow.Native.exe" "" "$INSTDIR\client\MangaFlow.Native.exe" 0
  !endif
  WriteUninstaller "$INSTDIR\unins000.exe"
SectionEnd

Section "Uninstall"
  ; 只删安装目录内容。用户数据（$LOCALAPPDATA\MangaFlow\Native）与旧壳数据
  ; （$LOCALAPPDATA\com.mangaflow.desktop）一律保留，升级与回退都靠它们。
  RMDir /r "$INSTDIR\client"
  RMDir /r "$INSTDIR\apps"
  RMDir /r "$INSTDIR\.venv-desktop"
  Delete "$INSTDIR\unins000.exe"
  RMDir "$INSTDIR"

  !ifndef NO_SHORTCUTS
    Delete "$DESKTOP\MangaFlow.lnk"
    Delete "$SMPROGRAMS\MangaFlow\MangaFlow.lnk"
    RMDir "$SMPROGRAMS\MangaFlow"
  !endif
  DeleteRegKey HKCU "${APP_UNINSTALL_KEY}"
SectionEnd

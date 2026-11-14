; Inno Setup 安装脚本
; 编译：ISCC.exe installer.iss
; 产物：installer\Video2Text-Setup.exe

#define AppName "视频转录 · 文本精校"
#define AppNameEn "Video2Text"
#define AppVersion "1.0.0"
#define AppPublisher "hujiyo"
#define AppExe "Video2Text.exe"

[Setup]
AppId={{8E5A3C21-4F7B-4D9A-9C2E-1B6D8F3A7E52}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppNameEn}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=Video2Text-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 需要管理员权限，因为装到 Program Files
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#AppExe}
; 模型不打包，安装后由程序自己下载
DiskSpanning=no

[Languages]
Name: "chinesesimpl"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
; 主程序目录（PyInstaller onedir 产物）。模型不打包，由程序首次启动时下载。
Source: "dist\Video2Text\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "models\*,models2\*,__pycache__\*,*.pyc"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; _internal 是 PyInstaller 的运行时文件，卸载时由主程序目录一并删除。
; 模型放在 %LOCALAPPDATA%\Video2Text\models，不在此列，重装时无需重新下载。
Type: filesandordirs; Name: "{app}\_internal"

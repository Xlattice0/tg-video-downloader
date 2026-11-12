#define MyAppName "TG视频下载"
#define MyAppNameEn "TGVideoDownloader"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "Xlattice0"
#define MyAppUserModelID "Xlattice0.TGVideoDownloader"
#define MyAppURL "https://github.com/Xlattice0/tg-video-downloader"
#define MyAppExeName "TGVideoDownloader.exe"

[Setup]
AppId={{8F3C2A91-6B47-4E1D-9C0A-2D5E8F17B4A3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppNameEn}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=TGVideoDownloader-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app-icon.ico
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\app-icon.ico
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
MinVersion=10.0
AllowNoIcons=yes
UsePreviousAppDir=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："

[Files]
Source: "..\dist\TGVideoDownloader.exe"; DestDir: "{app}"; Flags: ignoreversion; BeforeInstall: PrepareExeReplace
Source: "..\app-icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; 清理 1.3.1 及更早版本在开始菜单里创建的同名文件夹，避免和快捷方式重复显示
Type: filesandordirs; Name: "{autoprograms}\{#MyAppName}"
Type: files; Name: "{app}\{#MyAppExeName}.old"
Type: files; Name: "{app}\{#MyAppExeName}.*.old"
; 清掉旧版按当前用户安装时留下的快捷方式（可能仍指向已删除的 D:\App\...，任务栏会变成空白页）
Type: files; Name: "{userprograms}\{#MyAppName}.lnk"
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app-icon.ico"; AppUserModelID: "{#MyAppUserModelID}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\app-icon.ico"; Tasks: desktopicon; AppUserModelID: "{#MyAppUserModelID}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked

[UninstallDelete]
Type: filesandordirs; Name: "{app}\downloads"
Type: files; Name: "{app}\.write_test"
Type: files; Name: "{app}\{#MyAppExeName}.old"
Type: files; Name: "{app}\{#MyAppExeName}.*.old"

[Code]
function CreateFileW(lpFileName: string; dwDesiredAccess, dwShareMode, lpSecurityAttributes, dwCreationDisposition, dwFlagsAndAttributes: Cardinal; hTemplateFile: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';

function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function FileLocked(const FileName: string): Boolean;
var
  H: THandle;
begin
  Result := False;
  if not FileExists(FileName) then
    Exit;
  { DELETE access + exclusive share; a running exe often still allows GENERIC_READ }
  H := CreateFileW(FileName, $00010000, 0, 0, 3, $80, 0);
  if H = THandle(-1) then
    Result := True
  else
    CloseHandle(H);
end;

procedure TaskKillApp;
var
  ResultCode: Integer;
  ExePath: string;
begin
  ExePath := ExpandConstant('{sysnative}\taskkill.exe');
  if not FileExists(ExePath) then
    ExePath := ExpandConstant('{sys}\taskkill.exe');
  Exec(ExePath, '/F /IM {#MyAppExeName} /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function UniqueOldName(const Dest: string): string;
var
  I: Integer;
begin
  Result := Dest + '.old';
  I := 2;
  while FileExists(Result) do
  begin
    Result := Dest + '.' + IntToStr(I) + '.old';
    Inc(I);
  end;
end;

procedure PrepareExeReplace;
var
  Dest, Backup: string;
  I: Integer;
begin
  Dest := ExpandConstant('{app}\{#MyAppExeName}');
  if not FileExists(Dest) then
    Exit;
  TaskKillApp;
  I := 0;
  while (I < 8) and FileExists(Dest) and FileLocked(Dest) do
  begin
    Sleep(200);
    TaskKillApp;
    Inc(I);
  end;
  if not FileExists(Dest) then
    Exit;
  DeleteFile(Dest);
  if not FileExists(Dest) then
    Exit;
  { 有些残留进程无法结束，但可以把正在使用的 exe 改名，新文件就能写进去 }
  Backup := UniqueOldName(Dest);
  if not RenameFile(Dest, Backup) then
    RaiseException('无法替换已在使用的 TGVideoDownloader.exe。请注销或重启后再安装。');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  TaskKillApp;
  Result := '';
end;

function InitializeUninstall(): Boolean;
var
  Dest: string;
begin
  TaskKillApp;
  Dest := ExpandConstant('{app}\{#MyAppExeName}');
  if FileExists(Dest) then
  begin
    DeleteFile(Dest);
    if FileExists(Dest) then
      RenameFile(Dest, UniqueOldName(Dest));
  end;
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    DeleteFile(ExpandConstant('{userprograms}\{#MyAppName}.lnk'));
    DeleteFile(ExpandConstant('{userdesktop}\{#MyAppName}.lnk'));
    Exec(ExpandConstant('{sys}\ie4uinit.exe'), '-show', '', SW_HIDE, ewNoWait, ResultCode);
  end;
end;

; GridSim-Setup.exe: the installer of the GridSim Windows program (Inno Setup 6.7.x).
;
; Built by packaging\ci\build.ps1 on GitHub Actions:
;     ISCC /Q "/DAppVersion=2026.11.12" "/DStageDir=<repo>\dist\GridSim" packaging\gridsim.iss
; StageDir holds what gets installed:  python\ (the private Python with every library),  program\ (GridSim's own
; files, as in the source folder)  and  gridsim.ico.
;
; What it does on the PC, for the signed-in user only (no administrator rights, no questions):
;   - installs into %LOCALAPPDATA%\Programs\GridSim;
;   - before changing anything, pauses GridSim's scheduled jobs and ends every process that runs from the old
;     copy (if one cannot be ended, it stops there and nothing has been changed);
;   - wipes the old python\ and the old program code, so no file of an earlier version stays behind;
;     program\data (the downloaded football data) is kept;
;   - puts GridSim in the Start menu and on the desktop;
;   - points the scheduled jobs the user already had at the new program;
;   - /RELAUNCH=1 (used by the in-app update) starts GridSim again when a silent install has finished.
; It never reads, moves or deletes the data folder (C:\Users\<name>\GridSim Data). Neither does the uninstaller.
;
; Not used on purpose: AppMutex (it cancels a silent update while GridSim is open) and the Restart Manager
; (CloseApplications; it does not see .pyd files or windowless processes).
; The AppId must never change: it is how Windows knows a new installer is an update of the same program.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir "..\dist\GridSim"
#endif

[Setup]
AppId={{8F6B5C0E-3B7A-4E0B-9C55-6C1D7A2E4F10}
AppName=GridSim
AppVersion={#AppVersion}
AppVerName=GridSim {#AppVersion}
AppPublisher=GridSim
AppPublisherURL=https://github.com/Dierks27/GameSim
AppSupportURL=https://github.com/Dierks27/GameSim
VersionInfoVersion={#AppVersion}
VersionInfoDescription=GridSim installer
DefaultDirName={localappdata}\Programs\GridSim
DisableDirPage=yes
DisableProgramGroupPage=yes
DirExistsWarning=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
SetupIconFile=gridsim.ico
UninstallDisplayIcon={app}\gridsim.ico
UninstallDisplayName=GridSim
OutputDir=..\dist
OutputBaseFilename=GridSim-Setup
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
TimeStampsInUTC=yes
CloseApplications=no
RestartApplications=no
SetupLogging=yes
; Inno Setup 6.7 switches Windows' RedirectionGuard on for everything Setup starts (GridSim's first start from the
; last page, the relaunch after an update). Off, so a first start behaves like every later one. build.ps1 compiles
; with /DNoRedirectionGuard=1 if a compiler does not know the directive.
#ifndef NoRedirectionGuard
RedirectionGuard=no
#endif

[InstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\program\gridsim"
Type: filesandordirs; Name: "{app}\program\app"

[Files]
; these two stay first: PrepareToInstall extracts the first one before anything is installed
Source: "stop-gridsim.ps1"; Flags: dontcopy
Source: "stop-gridsim.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "{#StageDir}\gridsim.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\program\*"; DestDir: "{app}\program"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\GridSim"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gridsim.desktop"; WorkingDir: "{app}\program"; IconFilename: "{app}\gridsim.ico"; Comment: "GridSim"; AppUserModelID: "Dierks27.GridSim"
Name: "{autodesktop}\GridSim"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m gridsim.desktop"; WorkingDir: "{app}\program"; IconFilename: "{app}\gridsim.ico"; Comment: "GridSim"; AppUserModelID: "Dierks27.GridSim"

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gridsim schedule --repoint"; WorkingDir: "{app}\program"; StatusMsg: "Updating background jobs..."; Flags: runhidden
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gridsim.desktop"; WorkingDir: "{app}\program"; Description: "Start GridSim now"; Flags: nowait postinstall skipifsilent
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gridsim.desktop"; WorkingDir: "{app}\program"; Flags: nowait skipifnotsilent; Check: WantsRelaunch

[UninstallRun]
Filename: "{app}\python\pythonw.exe"; Parameters: "-m gridsim schedule --remove"; WorkingDir: "{app}\program"; Flags: runhidden skipifdoesntexist; RunOnceId: "RemoveSchedule"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\tools\stop-gridsim.ps1"" -Dir ""{app}"""; Flags: runhidden; RunOnceId: "StopGridSim"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\program"
Type: filesandordirs; Name: "{app}\tools"
Type: dirifempty; Name: "{app}"

[Code]
var
  TasksPaused: Boolean;

function WantsRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

{ Stop a scheduled job that is running right now and keep it from starting during the install.
  "gridsim schedule --repoint" (run right after the files are copied) switches the jobs back on.
  Jobs that do not exist: no effect. }
procedure QuietTask(TaskName: String);
var
  RC: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/End /TN "' + TaskName + '"', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Change /TN "' + TaskName + '" /DISABLE', '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

{ The install is not going ahead: switch the jobs that QuietTask paused back on, so they are not left off. }
procedure WakeTask(TaskName: String);
var
  RC: Integer;
begin
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Change /TN "' + TaskName + '" /ENABLE', '', SW_HIDE, ewWaitUntilTerminated, RC);
end;

{ stop-gridsim.ps1 ends with code 3 when something is still running from the old copy after 10 seconds. Only that
  code stops the install (before anything has been changed). Any other outcome - also a PC on which the script
  could not be run at all - lets the install go on: nothing running is the normal case. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  Result := '';
  QuietTask('GridSim Weekly');
  QuietTask('GridSim Predict');
  QuietTask('GridSim Scan');
  QuietTask('GridSim Watch');
  TasksPaused := True;
  ExtractTemporaryFile('stop-gridsim.ps1');
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\stop-gridsim.ps1') + '" -Dir "' + ExpandConstant('{app}') + '"',
      '', SW_HIDE, ewWaitUntilTerminated, RC) and (RC = 3) then
  begin
    WakeTask('GridSim Weekly');
    WakeTask('GridSim Predict');
    WakeTask('GridSim Scan');
    WakeTask('GridSim Watch');
    TasksPaused := False;
    Result := 'GridSim is still running and could not be closed. Close GridSim, then run this installer again.';
  end;
end;

{ The files are in place: from here "gridsim schedule --repoint" (run next) switches the jobs back on. }
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    TasksPaused := False;
end;

{ Setup is closing. If the install was cancelled or stopped half-way (nothing was installed, so nothing runs
  --repoint), the jobs the user already had must not stay switched off. }
procedure DeinitializeSetup();
begin
  if TasksPaused then
  begin
    WakeTask('GridSim Weekly');
    WakeTask('GridSim Predict');
    WakeTask('GridSim Scan');
    WakeTask('GridSim Watch');
  end;
end;

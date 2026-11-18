#ifndef AppVersion
  #error AppVersion must be provided by scripts/package_release.py
#endif
#ifndef SourceDir
  #error SourceDir must be provided by scripts/package_release.py
#endif
#ifndef OutputDir
  #error OutputDir must be provided by scripts/package_release.py
#endif

#define AppIdValue "{381B261E-B659-4B56-855E-ADDE80CC5BD2}"
#define RunMarker "Local\paper-reader-installer-running-v1"
#define InstallerName "paper-reader-" + AppVersion + "-windows-x64-setup"
#define BinaryVersion GetFileVersionString(SourceDir + "\paper-reader.exe")

#if BinaryVersion != AppVersion + ".0"
  #error The audited paper-reader.exe version does not match AppVersion
#endif

[Setup]
AppId={{381B261E-B659-4B56-855E-ADDE80CC5BD2}
AppName=页舟 · FolioSkiff
AppVersion={#AppVersion}
AppVerName=页舟 · FolioSkiff {#AppVersion}
AppPublisher=FolioSkiff contributors
AppPublisherURL=https://github.com/Lisalsj/folioskiff
AppSupportURL=https://github.com/Lisalsj/folioskiff/issues
AppUpdatesURL=https://github.com/Lisalsj/folioskiff/releases
AppCopyright=Licensed under Apache-2.0
MinVersion=10.0.22000
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\paper-reader
DefaultGroupName=FolioSkiff
AllowNoIcons=no
AllowNetworkDrive=no
AllowRootDirectory=no
AllowUNCPath=no
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
Uninstallable=yes
CreateUninstallRegKey=yes
UninstallLogMode=append
UninstallDisplayName=页舟 · FolioSkiff {#AppVersion}
UninstallDisplayIcon={app}\paper-reader.exe
AllowCancelDuringInstall=no
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
AppMutex=Local\paper-reader-installer-running-v1
ChangesAssociations=no
ChangesEnvironment=no
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#SourcePath}\paper-reader.ico
LicenseFile={#SourceDir}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename={#InstallerName}
SignedUninstaller=no
VersionInfoCompany=FolioSkiff contributors
VersionInfoCopyright=Licensed under Apache-2.0
VersionInfoDescription=FolioSkiff Windows installer
VersionInfoOriginalFileName={#InstallerName}.exe
VersionInfoProductName=页舟 · FolioSkiff
VersionInfoProductVersion={#AppVersion}.0
VersionInfoTextVersion={#AppVersion}.0
VersionInfoVersion={#AppVersion}.0

[Tasks]
Name: desktopicon; Description: "Create a &desktop icon"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FolioSkiff"; Filename: "{app}\paper-reader.exe"; Parameters: "serve"; WorkingDir: "{app}"; IconFilename: "{app}\paper-reader.exe"
Name: "{autodesktop}\FolioSkiff"; Filename: "{app}\paper-reader.exe"; Parameters: "serve"; WorkingDir: "{app}"; IconFilename: "{app}\paper-reader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\paper-reader.exe"; Parameters: "serve"; WorkingDir: "{app}"; Description: "Launch FolioSkiff"; Flags: postinstall nowait skipifsilent unchecked

[Code]
const
  UninstallRegistryKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{381B261E-B659-4B56-855E-ADDE80CC5BD2}_is1';
  PR_FILE_ATTRIBUTE_DIRECTORY = $00000010;
  PR_FILE_ATTRIBUTE_REPARSE_POINT = $00000400;
  PR_INVALID_FILE_ATTRIBUTES = $FFFFFFFF;
  MaxCleanupDepth = 64;
  MaxCleanupEntries = 100000;

var
  CleanupLocalData: Boolean;
  CleanupResearchData: Boolean;
  CleanupEntryCount: Integer;
  CleanupForm: TSetupForm;
  CleanupLocalCheck: TNewCheckBox;
  CleanupResearchCheck: TNewCheckBox;
  CleanupConfirmationLabel: TNewStaticText;
  CleanupConfirmation: TNewEdit;

function GetFileAttributesW(lpFileName: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function InstalledVersionIsNewer: Boolean;
var
  InstalledVersion: String;
  InstalledPacked: Int64;
  CandidatePacked: Int64;
begin
  Result := False;
  if not RegQueryStringValue(
    HKCU,
    UninstallRegistryKey,
    'DisplayVersion',
    InstalledVersion
  ) then
    exit;

  if not StrToVersion(InstalledVersion, InstalledPacked) then
  begin
    Log('Installed FolioSkiff version is invalid; downgrade safety is closed.');
    Result := True;
    exit;
  end;
  if not StrToVersion('{#AppVersion}.0', CandidatePacked) then
  begin
    Log('Candidate FolioSkiff version is invalid.');
    Result := True;
    exit;
  end;
  Result := ComparePackedVersion(InstalledPacked, CandidatePacked) > 0;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;
  Result := '';
  if CheckForMutexes('{#RunMarker}') then
  begin
    Result := 'FolioSkiff is running. Close it normally before installing or upgrading; Setup will not force it to close.';
    exit;
  end;
  if InstalledVersionIsNewer then
  begin
    Result := 'The installed version is newer than this package. Downgrade is blocked to protect the local database.';
    exit;
  end;
end;

procedure CleanupResearchClick(Sender: TObject);
begin
  CleanupConfirmationLabel.Enabled := CleanupResearchCheck.Checked;
  CleanupConfirmation.Enabled := CleanupResearchCheck.Checked;
end;

procedure CleanupOkClick(Sender: TObject);
begin
  if CleanupResearchCheck.Checked and
    (CompareText(Trim(CleanupConfirmation.Text), 'DELETE PAPER DATA') <> 0) then
  begin
    MsgBox(
      'Type DELETE PAPER DATA exactly before selecting paper library and backup removal.',
      mbError,
      MB_OK
    );
    exit;
  end;
  CleanupLocalData := CleanupLocalCheck.Checked;
  CleanupResearchData := CleanupResearchCheck.Checked;
  TNewButton(Sender).ModalResult := mrOk;
end;

function ShowCleanupOptions: Boolean;
var
  Intro: TNewStaticText;
  OkButton: TNewButton;
  CancelButton: TNewButton;
begin
  CleanupForm := CreateCustomForm(ScaleX(520), ScaleY(280), False, True);
  try
    CleanupForm.Caption := 'FolioSkiff data cleanup';

    Intro := TNewStaticText.Create(CleanupForm);
    Intro.Parent := CleanupForm;
    Intro.Left := ScaleX(20);
    Intro.Top := ScaleY(20);
    Intro.Width := ScaleX(480);
    Intro.Height := ScaleY(44);
    Intro.AutoSize := False;
    Intro.WordWrap := True;
    Intro.Caption := 'Uninstall removes only program files and shortcuts by default. Optional private data cleanup is listed separately below.';

    CleanupLocalCheck := TNewCheckBox.Create(CleanupForm);
    CleanupLocalCheck.Parent := CleanupForm;
    CleanupLocalCheck.Left := ScaleX(20);
    CleanupLocalCheck.Top := ScaleY(78);
    CleanupLocalCheck.Width := ScaleX(480);
    CleanupLocalCheck.Caption := 'Also remove downloaded models, cache, and logs from the default local folder';
    CleanupLocalCheck.Checked := False;

    CleanupResearchCheck := TNewCheckBox.Create(CleanupForm);
    CleanupResearchCheck.Parent := CleanupForm;
    CleanupResearchCheck.Left := ScaleX(20);
    CleanupResearchCheck.Top := ScaleY(112);
    CleanupResearchCheck.Width := ScaleX(480);
    CleanupResearchCheck.Caption := 'Also remove paper library and backups from the default Documents folder';
    CleanupResearchCheck.Checked := False;
    CleanupResearchCheck.OnClick := @CleanupResearchClick;

    CleanupConfirmationLabel := TNewStaticText.Create(CleanupForm);
    CleanupConfirmationLabel.Parent := CleanupForm;
    CleanupConfirmationLabel.Left := ScaleX(40);
    CleanupConfirmationLabel.Top := ScaleY(148);
    CleanupConfirmationLabel.Width := ScaleX(440);
    CleanupConfirmationLabel.Caption := 'Type DELETE PAPER DATA to confirm research data removal:';

    CleanupConfirmation := TNewEdit.Create(CleanupForm);
    CleanupConfirmation.Parent := CleanupForm;
    CleanupConfirmation.Left := ScaleX(40);
    CleanupConfirmation.Top := ScaleY(170);
    CleanupConfirmation.Width := ScaleX(440);
    CleanupConfirmation.Text := '';

    OkButton := TNewButton.Create(CleanupForm);
    OkButton.Parent := CleanupForm;
    OkButton.Left := ScaleX(324);
    OkButton.Top := ScaleY(230);
    OkButton.Width := ScaleX(80);
    OkButton.Caption := 'Continue';
    OkButton.Default := True;
    OkButton.ModalResult := mrNone;
    OkButton.OnClick := @CleanupOkClick;

    CancelButton := TNewButton.Create(CleanupForm);
    CancelButton.Parent := CleanupForm;
    CancelButton.Left := ScaleX(416);
    CancelButton.Top := ScaleY(230);
    CancelButton.Width := ScaleX(80);
    CancelButton.Caption := 'Cancel';
    CancelButton.Cancel := True;
    CancelButton.ModalResult := mrCancel;

    CleanupResearchClick(nil);
    Result := CleanupForm.ShowModal() = mrOk;
  finally
    CleanupForm.Free();
  end;
end;

function InitializeUninstall: Boolean;
begin
  CleanupLocalData := False;
  CleanupResearchData := False;
  if UninstallSilent then
  begin
    Result := True;
    exit;
  end;
  Result := ShowCleanupOptions;
end;

function TreeIsSafe(const Root: String; const Depth: Integer): Boolean;
var
  Attributes: LongWord;
  FindRec: TFindRec;
  Child: String;
begin
  Result := False;
  if Depth > MaxCleanupDepth then
    exit;
  Attributes := GetFileAttributesW(Root);
  if Attributes = PR_INVALID_FILE_ATTRIBUTES then
  begin
    Result := True;
    exit;
  end;
  if ((Attributes and PR_FILE_ATTRIBUTE_DIRECTORY) = 0) or
    ((Attributes and PR_FILE_ATTRIBUTE_REPARSE_POINT) <> 0) then
    exit;

  if FindFirst(AddBackslash(Root) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          CleanupEntryCount := CleanupEntryCount + 1;
          if CleanupEntryCount > MaxCleanupEntries then
            exit;
          if (FindRec.Attributes and PR_FILE_ATTRIBUTE_REPARSE_POINT) <> 0 then
            exit;
          if (FindRec.Attributes and PR_FILE_ATTRIBUTE_DIRECTORY) <> 0 then
          begin
            Child := AddBackslash(Root) + FindRec.Name;
            if not TreeIsSafe(Child, Depth + 1) then
              exit;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  Result := True;
end;

function IsApprovedCleanupRoot(const Root: String): Boolean;
begin
  Result :=
    (CompareText(Root, ExpandConstant('{localappdata}\paper-reader\models')) = 0) or
    (CompareText(Root, ExpandConstant('{localappdata}\paper-reader\cache')) = 0) or
    (CompareText(Root, ExpandConstant('{localappdata}\paper-reader\logs')) = 0) or
    (CompareText(Root, ExpandConstant('{userdocs}\paper-reader\objects')) = 0) or
    (CompareText(Root, ExpandConstant('{userdocs}\paper-reader\backups')) = 0);
end;

procedure SafeDeleteTree(const Root: String; const Category: String);
begin
  if not IsApprovedCleanupRoot(Root) then
  begin
    Log('Skipped an unapproved FolioSkiff cleanup root for ' + Category + '.');
    exit;
  end;
  CleanupEntryCount := 0;
  if not TreeIsSafe(Root, 0) then
  begin
    Log('Skipped FolioSkiff ' + Category + ' cleanup because a boundary was unsafe.');
    exit;
  end;
  if DirExists(Root) and not DelTree(Root, True, True, True) then
    Log('FolioSkiff could not completely remove the selected ' + Category + ' data.');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  if CleanupLocalData then
  begin
    SafeDeleteTree(ExpandConstant('{localappdata}\paper-reader\models'), 'model');
    SafeDeleteTree(ExpandConstant('{localappdata}\paper-reader\cache'), 'cache');
    SafeDeleteTree(ExpandConstant('{localappdata}\paper-reader\logs'), 'log');
  end;
  if CleanupResearchData then
  begin
    SafeDeleteTree(ExpandConstant('{userdocs}\paper-reader\objects'), 'paper library');
    SafeDeleteTree(ExpandConstant('{userdocs}\paper-reader\backups'), 'backup');
  end;
end;

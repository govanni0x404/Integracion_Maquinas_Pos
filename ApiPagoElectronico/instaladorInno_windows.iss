#define MyAppName "apiPosIntegrado"
#define MyAppVersion "1.0"
#define MyAppExeName "apiPosIntegrado.exe"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName=C:\apiPosIntegrado
DisableDirPage=yes
DefaultGroupName={#MyAppName}
OutputDir=.
OutputBaseFilename=Instalador_apiPosIntegrado
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
Source: "C:\apiPosIntegrado\apiPosIntegrado.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\apiPosIntegrado\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Icons]
Name: "{commondesktop}\apiPosIntegrado"; Filename: "{app}\apiPosIntegrado.exe"
Name: "{group}\apiPosIntegrado"; Filename: "{app}\apiPosIntegrado.exe"

[Run]
Filename: "{app}\apiPosIntegrado.exe"; Description: "Ejecutar apiPosIntegrado"; Flags: nowait postinstall skipifsilent
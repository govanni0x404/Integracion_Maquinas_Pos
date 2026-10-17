; Instalador Windows de ApiPagoElectronico (Inno Setup 6).
; Empaqueta la salida de compilar_windows.bat: dist\ApiPagoElectronico.exe, dist\.env y dist\assets\.
; Compilar este .iss desde la raíz del proyecto DESPUÉS de correr compilar_windows.bat.

#define MyAppName "ApiPagoElectronico"
#define MyAppVersion "1.0.0"
#define MyAppExeName "ApiPagoElectronico.exe"
; Mismo nombre de valor que usa el panel (/panel/autostart) en HKCU\...\Run: APP_NAME de config/settings.py
#define AutostartValueName " ApiPagoElectronico"

[Setup]
AppId={{A1E1C9A1-7B02-4E38-9D5A-1E8F3A2E0B11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppName}
; Instalación por usuario (%LOCALAPPDATA%\Programs): la app escribe el .env junto al
; .exe y crea logs\ y data\ (base de datos y lock) al lado; en Program Files no tendría permisos.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=.
OutputBaseFilename=Instalador_{#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear icono en el escritorio"; GroupDescription: "Accesos directos:"
Name: "autostart"; Description: "Iniciar con Windows (usuario actual)"; GroupDescription: "Opciones:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; El .env solo se copia en la primera instalación: una actualización no pisa la configuración de la caja.
Source: "dist\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "dist\assets\*"; DestDir: "{app}\assets"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Panel {#MyAppName}"; Filename: "http://localhost:5005/panel"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AutostartValueName}"; \
  ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Ejecutar {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden; RunOnceId: "StopApp"

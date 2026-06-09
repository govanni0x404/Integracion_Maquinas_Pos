# Instalador Inno Setup 6.7 — ApiPagoElectronico

Este documento describe cómo construir un instalador Windows con Inno Setup 6.7 para distribuir **ApiPagoElectronico** (ejecutable PyInstaller) junto a su `.env`, assets y accesos directos.

Objetivo:

- Instalar en `Program Files` (o carpeta elegida).
- Copiar `ApiPagoElectronico.exe`, `assets\` y un `.env` inicial.
- Crear acceso directo.
- (Opcional) Habilitar inicio con Windows.
- Evitar ventanas CMD innecesarias: el `.exe` se compila con `--windowed` y el instalador ejecuta en modo silencioso cuando corresponde.

---

## 1) Prerrequisitos

1. Inno Setup 6.7 instalado.
2. Compilación Windows lista (salida de `compilar_windows.bat`):
   - `dist\ApiPagoElectronico.exe`
   - `dist\.env`
   - `dist\assets\` (si aplica)

Notas:

- El `.env` debe contener `MP_ACCESS_TOKEN=` vacío o configurado según la caja. Se recomienda instalar con token vacío y que el usuario lo configure en `/panel`.
- El instalador NO debe hardcodear el token en el script.

---

## 2) Estructura sugerida del paquete a instalar

Usa una carpeta “staging” (ejemplo `installer\payload\`) con:

- `ApiPagoElectronico.exe`
- `.env`
- `assets\*`

Esto evita que el instalador tome cosas adicionales del repo.

---

## 3) Script Inno Setup (.iss) base

Guarda este contenido como `ApiPagoElectronico.iss` (donde prefieras) y ajusta rutas.

```ini
[Setup]
AppId={{A1E1C9A1-7B02-4E38-9D5A-1E8F3A2E0B11}}
AppName=ApiPagoElectronico
AppVersion=1.0.0
AppPublisher=ApiPagoElectronico
DefaultDirName={autopf}\ApiPagoElectronico
DefaultGroupName=ApiPagoElectronico
DisableProgramGroupPage=yes
OutputBaseFilename=ApiPagoElectronico_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear icono en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked
Name: "autostart"; Description: "Iniciar con Windows (usuario actual)"; GroupDescription: "Opciones:"; Flags: unchecked

[Files]
Source: "installer\payload\ApiPagoElectronico.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer\payload\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "installer\payload\assets\*"; DestDir: "{app}\assets"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: DirExists(ExpandConstant('{src}\installer\payload\assets'))

[Icons]
Name: "{group}\ApiPagoElectronico"; Filename: "{app}\ApiPagoElectronico.exe"
Name: "{commondesktop}\ApiPagoElectronico"; Filename: "{app}\ApiPagoElectronico.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ApiPagoElectronico.exe"; Description: "Ejecutar ApiPagoElectronico"; Flags: nowait postinstall skipifsilent

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ApiPagoElectronico"; \
  ValueData: """{app}\ApiPagoElectronico.exe"""; Tasks: autostart

[Code]
function DirExists(const Dir: string): Boolean;
begin
  Result := FileExists(Dir + '\') or (GetFileAttributes(Dir) <> $FFFFFFFF);
end;
```

### 3.1 Puntos importantes del script

- `PrivilegesRequired=lowest`: evita requerir administrador (instala por usuario). Si quieres instalar para todos los usuarios, cambia a `admin`.
- `.env` se copia con `onlyifdoesntexist`: si el usuario ya configuró el token, una reinstalación no lo pisa.
- `autostart` se configura con una entrada `HKCU\...\Run` solo si el usuario marca la tarea.

---

## 4) Recomendaciones de configuración

### 4.1 `.env` en el instalador

Recomendación:

- Distribuir `.env` con `MP_ACCESS_TOKEN=` vacío.
- El usuario lo configura en el panel: `http://localhost:<HTTP_PORT>/panel` en el campo `MP_ACCESS_TOKEN`.

Esto evita exponer el token en el instalador.

### 4.2 Puerto y firewall (Windows)

El servicio escucha en `HTTP_PORT` (default 5005).

- Si la caja necesita accesos desde la red local, debes abrir el puerto en firewall. El proyecto ya intenta crear regla automáticamente (netsh) cuando corre con permisos suficientes.

---

## 5) Compilación del instalador

1. Copia el payload al path definido en `[Files]`.
2. Abre Inno Setup 6.7.
3. `File → Open` tu `.iss`.
4. `Build → Compile`.
5. El instalador resultante se genera en el directorio de salida de Inno (según `OutputDir` si lo defines, o junto al `.iss`).

---

## 6) Validación rápida post-instalación

1. Ejecutar `ApiPagoElectronico` desde el acceso directo.
2. Abrir `http://localhost:5005/status`.
3. Abrir `http://localhost:5005/panel`.
4. Verificar que el `.env` exista en `{app}\.env`.
5. Configurar `MP_ACCESS_TOKEN` desde el panel si corresponde.
6. (Opcional) Marcar “Iniciar con Windows” y reiniciar sesión para validar autostart.

---

## 7) Solución de problemas

- Si aparece error de `psutil._psutil_windows` al iniciar:
  - Recompilar con `compilar_windows.bat` actualizado que incluye `psutil` en PyInstaller.
- Si Mercado Pago responde “Falta configuración MP_ACCESS_TOKEN”:
  - Configurar `MP_ACCESS_TOKEN` en `{app}\.env` o desde el panel.


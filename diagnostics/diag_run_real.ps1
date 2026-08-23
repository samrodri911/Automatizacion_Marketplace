# diag_run_real.ps1
# Ejecuta Marketplace Manager REAL bajo procdump con MM_FORENSICS=1 para
# capturar el minidump del crash nativo (0xC0000409 / EPIPE).
#
# - No modifica el flujo de la aplicación.
# - Guarda el dump en %TEMP%\opencode\werdumps con timestamp.
# - Guarda versiones y el log de consola en la misma carpeta.
#
# USO: abrir PowerShell y ejecutar:
#     powershell -NoProfile -ExecutionPolicy Bypass -File diagnostics\diag_run_real.ps1

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$env:MM_FORENSICS = "1"

$dumpDir = "C:\Users\User\AppData\Local\Temp\opencode\werdumps"
New-Item -ItemType Directory -Force -Path $dumpDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

# --- Trazabilidad de versiones ---
$verFile = Join-Path $dumpDir "versiones_$stamp.txt"
@"
=== Marketplace Manager - Diagnostico 0xC0000409 ===
Fecha de ejecucion: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Windows            : $([System.Environment]::OSVersion.VersionString)
Python             : $(& "$repo\.venv\Scripts\python.exe" -V 2>&1)
Ruta python        : $repo\.venv\Scripts\python.exe
MM_FORENSICS       : $env:MM_FORENSICS
procdump           : C:\Users\User\AppData\Local\Temp\opencode\procdump64.exe

=== Paquetes ===
"@ | Set-Content -Encoding UTF8 $verFile
& "$repo\.venv\Scripts\python.exe" -m pip show playwright greenlet PySide6 2>$null |
    Select-String -Pattern "^Name:|^Version:" | ForEach-Object { $_.Line } |
    Out-File -Append -Encoding UTF8 $verFile

Write-Host ""
Write-Host "=============================================================="
Write-Host "  Marketplace Manager BAJO PROCDUMP  |  MM_FORENSICS=1"
Write-Host "  Dumps    : $dumpDir"
Write-Host "  Versiones: $verFile"
Write-Host "  Reproduce ahora: Republicar -> confirmar -> iniciar eliminacion"
Write-Host "  Si crashea, NO reinicies la app hasta que este script termine."
Write-Host "=============================================================="
Write-Host ""

# Consola de la app redirigida a archivos (traceback / consola).
# La app escribe sus logs a stderr; Start-Process redirige stdout/stderr sin
# que PowerShell los convierta en NativeCommandError.
$consoleOut = Join-Path $dumpDir "consola_$stamp.txt"
$stdOutFile = Join-Path $dumpDir "_stdout_$stamp.txt"
$stdErrFile = Join-Path $dumpDir "_stderr_$stamp.txt"

$procdump = "C:\Users\User\AppData\Local\Temp\opencode\procdump64.exe"
$pythonExe = "$repo\.venv\Scripts\python.exe"
$args = @(
    "-accepteula", "-ma", "-e", "1", "-f", "c0000005,c0000409", "-t", "-x", $dumpDir,
    "`"$pythonExe`"", "-u", "main.py"
)
$proc = Start-Process -FilePath $procdump -ArgumentList $args -Wait -PassThru `
    -RedirectStandardOutput $stdOutFile -RedirectStandardError $stdErrFile

# Consolida stdout + stderr en un solo archivo de consola
Copy-Item $stdErrFile $consoleOut -Force
Get-Content $stdOutFile | Out-File -Append -Encoding utf8 $consoleOut
Remove-Item $stdOutFile, $stdErrFile -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Proceso terminado (exit code: $($proc.ExitCode)) ==="
$dumps = Get-ChildItem $dumpDir -Filter *.dmp | Sort-Object CreationTime
if ($dumps.Count -gt 0) {
    Write-Host "Dumps generados:"
    $dumps | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,1)}}, CreationTime | Format-Table -AutoSize
    Write-Host "Para analizar el dump:"
    Write-Host "  .venv\Scripts\python.exe diagnostics\analyze_dump.py <ruta_del_dmp>"
} else {
    Write-Host "NO se genero dump (la app no crasheo con 0xC0000409)."
}
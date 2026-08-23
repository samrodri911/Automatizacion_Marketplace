# diag_run_real_clean.ps1
# Ejecuta Marketplace Manager REAL SIN procdump (solo el VEH interno de
# main.py) con MM_FORENSICS=1, para VALIDAR el fix del crash 0xC0000005.
#
# - Sin debugger externo: descarta la contaminación de timing de procdump.
# - El VEH de la app sigue activo: si el fix falla, se genera el .dmp.
# - Guarda versiones y el log de consola en %TEMP%\opencode\werdumps.
#
# USO:
#     powershell -NoProfile -ExecutionPolicy Bypass -File diagnostics\diag_run_real_clean.ps1

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$env:MM_FORENSICS = "1"

$dumpDir = "C:\Users\User\AppData\Local\Temp\opencode\werdumps"
New-Item -ItemType Directory -Force -Path $dumpDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"

$verFile = Join-Path $dumpDir "versiones_$stamp.txt"
@"
=== Marketplace Manager - Validacion fix 0xC0000005 (SIN procdump) ===
Fecha de ejecucion: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Windows            : $([System.Environment]::OSVersion.VersionString)
Python             : $(& "$repo\.venv\Scripts\python.exe" -V 2>&1)
Ruta python        : $repo\.venv\Scripts\python.exe
MM_FORENSICS       : $env:MM_FORENSICS
procdump           : NO (validacion limpia)

=== Paquetes ===
"@ | Set-Content -Encoding UTF8 $verFile
& "$repo\.venv\Scripts\python.exe" -m pip show playwright greenlet PySide6 2>$null |
    Select-String -Pattern "^Name:|^Version:" | ForEach-Object { $_.Line } |
    Out-File -Append -Encoding UTF8 $verFile

Write-Host ""
Write-Host "=============================================================="
Write-Host "  Marketplace Manager VALIDACION SIN PROCDUMP | MM_FORENSICS=1"
Write-Host "  Reproduce ahora: Republicar -> confirmar -> iniciar eliminacion"
Write-Host "  Si el fix falla, el VEH generara un dump en $dumpDir"
Write-Host "=============================================================="
Write-Host ""

$consoleOut = Join-Path $dumpDir "consola_$stamp.txt"
$stdOutFile = Join-Path $dumpDir "_stdout_$stamp.txt"
$stdErrFile = Join-Path $dumpDir "_stderr_$stamp.txt"

$pythonExe = "$repo\.venv\Scripts\python.exe"
$proc = Start-Process -FilePath $pythonExe -ArgumentList "-u", "main.py" -Wait -PassThru `
    -RedirectStandardOutput $stdOutFile -RedirectStandardError $stdErrFile

Copy-Item $stdErrFile $consoleOut -Force
Get-Content $stdOutFile | Out-File -Append -Encoding utf8 $consoleOut
Remove-Item $stdOutFile, $stdErrFile -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Proceso terminado (exit code: $($proc.ExitCode)) ==="
$dumps = Get-ChildItem $dumpDir -Filter *.dmp | Sort-Object CreationTime
if ($dumps.Count -gt 0) {
    Write-Host "Dumps generados (revisar si corresponden a esta corrida):"
    $dumps | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,1)}}, CreationTime | Format-Table -AutoSize
} else {
    Write-Host "NO se genero dump: el fix se mantuvo (sin crash nativo)."
}
<#
  Builds SivaSafe.exe from SivaSafeLauncher.cs and puts a shortcut to it on
  the desktop.

      powershell -ExecutionPolicy Bypass -File launcher\build.ps1

  Only needed when the launcher source changes. Changes to SivaSafe itself
  need no rebuild — the exe runs main.py from this folder every time.

  The compiler is the one Windows already ships as part of the .NET Framework,
  so there is nothing to install.
#>

$ErrorActionPreference = 'Stop'

$launcherDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir  = Split-Path -Parent $launcherDir

$source = Join-Path $launcherDir 'SivaSafeLauncher.cs'
$icon   = Join-Path $projectDir  'assets\sivasafe.ico'
$output = Join-Path $projectDir  'SivaSafe.exe'

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) {
    $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path $csc)) {
    throw "No C# compiler found under $env:WINDIR\Microsoft.NET. Install the .NET Framework 4 developer files, or build the launcher elsewhere."
}

# /target:winexe is what keeps a console window from appearing behind the app.
$cscArgs = @(
    '/nologo'
    '/target:winexe'
    '/platform:anycpu'
    '/optimize+'
    "/out:$output"
    "/win32icon:$icon"
    '/reference:System.dll'
    '/reference:System.Windows.Forms.dll'
    '/reference:System.Management.dll'
    $source
)

Write-Host "Compiling $output ..."
& $csc $cscArgs
if ($LASTEXITCODE -ne 0) { throw "csc.exe failed with exit code $LASTEXITCODE" }

# Desktop shortcut. Rewritten every build so a moved project folder is picked
# up rather than leaving a shortcut pointing at nothing.
$desktop  = [Environment]::GetFolderPath('Desktop')
$linkPath = Join-Path $desktop 'SivaSafe.lnk'

$shell = New-Object -ComObject WScript.Shell
$link  = $shell.CreateShortcut($linkPath)
$link.TargetPath       = $output
$link.WorkingDirectory = $projectDir
$link.IconLocation     = "$icon,0"
$link.Description      = 'Start SivaSafe'
$link.Save()
[System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null

Write-Host "Built    : $output"
Write-Host "Shortcut : $linkPath"

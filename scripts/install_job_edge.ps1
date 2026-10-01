[CmdletBinding()]
param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'JobApplicationEdge'),
    [string]$ProfileDirectory = (Join-Path $env:LOCALAPPDATA 'Edge-Automation-CDP'),
    [ValidateRange(1024, 65535)][int]$Port = 9333,
    [string]$DesktopDirectory = [Environment]::GetFolderPath('Desktop'),
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Find-Edge {
    $candidates = @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA) |
        Where-Object { $_ } | ForEach-Object { Join-Path $_ 'Microsoft\Edge\Application\msedge.exe' }
    return $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}

$InstallDirectory = [IO.Path]::GetFullPath($InstallDirectory)
$ProfileDirectory = [IO.Path]::GetFullPath($ProfileDirectory).TrimEnd('\')
$defaultProfile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data')).TrimEnd('\')
if ($ProfileDirectory -eq $defaultProfile -or $ProfileDirectory.StartsWith($defaultProfile + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use a dedicated profile outside the normal Edge User Data directory.'
}
$configPath = Join-Path $InstallDirectory 'edge-config.json'
if (Test-Path -LiteralPath $configPath) {
    $existing = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($existing.profile_directory -ne $ProfileDirectory -or $existing.port -ne $Port) {
        throw "An existing installation uses different settings. Keep it or pass its profile and port explicitly: $configPath"
    }
}
$edge = Find-Edge
if (-not $edge) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw 'Install Microsoft Edge from https://www.microsoft.com/edge/download and run this installer again.'
    }
    Write-Host 'Installing Microsoft Edge from the Windows package catalog...'
    & winget.exe install --id Microsoft.Edge --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Microsoft Edge installation failed: $LASTEXITCODE" }
    $edge = Find-Edge
    if (-not $edge) { throw 'Microsoft Edge was not found after installation. Restart this installer.' }
}

$source = Join-Path $PSScriptRoot 'start_job_edge.ps1'
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw 'Missing start_job_edge.ps1. Extract the entire repository ZIP first.' }
New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $ProfileDirectory | Out-Null
$launcher = Join-Path $InstallDirectory 'start_job_edge.ps1'
if ([IO.Path]::GetFullPath($source) -ne $launcher) { Copy-Item -LiteralPath $source -Destination $launcher -Force }
@{
    edge_executable = $edge
    profile_directory = $ProfileDirectory
    port = $Port
    cdp_endpoint = "http://127.0.0.1:$Port"
} | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8

if (-not (Test-Path -LiteralPath $DesktopDirectory -PathType Container)) { throw "Desktop directory not found: $DesktopDirectory" }
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
# Construct the Chinese label without relying on PowerShell 5.1 source encoding.
$label = -join ([char[]]@(0x7F51, 0x7533, 0x4E13, 0x7528))
$shortcutPath = Join-Path $DesktopDirectory ($label + ' Edge.lnk')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcutArguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '" -ConfigPath "' + $configPath + '" -ShowErrors'
if ((Test-Path -LiteralPath $shortcutPath) -and ($shortcut.TargetPath -ne $powershell -or $shortcut.Arguments -ne $shortcutArguments)) {
    throw "An unrelated desktop shortcut already exists. Rename it before installing: $shortcutPath"
}
$shortcut.TargetPath = $powershell
$shortcut.Arguments = $shortcutArguments
$shortcut.WorkingDirectory = $InstallDirectory
$shortcut.IconLocation = "$edge,0"
$shortcut.Description = 'Dedicated Edge profile for job applications (localhost CDP)'
$shortcut.Save()
Write-Host "Desktop shortcut: $shortcutPath"
Write-Host "Browser profile: $ProfileDirectory"
Write-Host "Application endpoint: http://127.0.0.1:$Port"
if (-not $NoStart) { & $launcher -ConfigPath $configPath }
Write-Host 'Setup complete. Sign in to recruitment websites on this computer before applying.'

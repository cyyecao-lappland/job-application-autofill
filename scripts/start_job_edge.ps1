[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'edge-config.json'),
    [switch]$ShowErrors
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

try {
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $profile = [IO.Path]::GetFullPath([string]$config.profile_directory).TrimEnd('\')
    $port = [int]$config.port
    if ($port -lt 1024 -or $port -gt 65535) { throw 'Invalid CDP port.' }
    $defaultProfile = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\User Data')).TrimEnd('\')
    if ($profile -eq $defaultProfile -or $profile.StartsWith($defaultProfile + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The normal Edge profile cannot be used for job application debugging.'
    }
    if ($profile.Contains('"')) { throw 'Invalid profile path.' }
    if (-not (Test-Path -LiteralPath $config.edge_executable -PathType Leaf)) { throw 'Edge executable is missing. Run install-edge.cmd again.' }
    $endpoint = "http://127.0.0.1:$port"

    function Assert-DedicatedListener {
        $version = Invoke-RestMethod -Uri "$endpoint/json/version" -TimeoutSec 2
        if ($version.Browser -notmatch '^(Edg|Microsoft Edge)/') { throw 'The port belongs to a different browser.' }
        $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
        if ($listeners.Count -eq 0) { throw 'Cannot verify the browser listener.' }
        foreach ($listener in $listeners) {
            if ($listener.LocalAddress -notin @('127.0.0.1', '::1')) { throw 'The debug port is not restricted to localhost.' }
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
            if (-not $owner -or $owner.Name -ne 'msedge.exe') { throw 'The port owner is not Microsoft Edge.' }
            if (-not $owner.CommandLine -or $owner.CommandLine -notmatch '--user-data-dir=(?:"(?<profile>[^"]+)"|(?<profile>\S+))') {
                throw 'Cannot verify the browser profile.'
            }
            $actualProfile = [IO.Path]::GetFullPath($Matches.profile).TrimEnd('\')
            if ($actualProfile -ne $profile) { throw 'The port belongs to another Edge profile.' }
        }
    }

    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    if ($listeners.Count -gt 0) {
        Assert-DedicatedListener
        Write-Host "Dedicated Edge is already available: $endpoint"
        return
    }
    New-Item -ItemType Directory -Force -Path $profile | Out-Null
    Start-Process -FilePath $config.edge_executable -WindowStyle Normal -ArgumentList @(
        ('--user-data-dir="' + $profile + '"'),
        "--remote-debugging-port=$port",
        '--remote-debugging-address=127.0.0.1',
        '--no-first-run',
        'about:blank'
    )
    $deadline = [DateTime]::UtcNow.AddSeconds(25)
    do {
        try {
            Assert-DedicatedListener
            Write-Host "Dedicated Edge is ready: $endpoint"
            return
        } catch {
            $lastError = $_.Exception.Message
            Start-Sleep -Milliseconds 500
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Edge did not become ready: $lastError Close only this dedicated Edge window, then retry. An Edge policy may disable debugging."
} catch {
    if ($ShowErrors) {
        $shell = New-Object -ComObject WScript.Shell
        $null = $shell.Popup($_.Exception.Message, 0, 'Job application Edge', 16)
    }
    throw
}

param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArguments)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'MIGRATING.txt')) {
    throw 'Web application storage migration is still running. Wait for completion.'
}
$candidates = @(
    $env:JOB_APPLICATION_PYTHON,
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')
) | Where-Object { $_ }
$runtime = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $runtime) { throw 'Python runtime missing. Create .venv and install this project first.' }
Push-Location $PSScriptRoot
try {
    if ($CommandArguments.Count -gt 0 -and $CommandArguments[0] -eq 'application') {
        $applicationArguments = @($CommandArguments | Select-Object -Skip 1)
        & $runtime -m edge_form_graph.application_cli @applicationArguments
    } else {
        & $runtime -m edge_form_graph.cli @CommandArguments
    }
    exit $LASTEXITCODE
} finally { Pop-Location }

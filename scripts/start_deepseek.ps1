param(
    [string]$Python = 'python',
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
if (-not $env:DEEPSEEK_API_KEY) {
    $env:DEEPSEEK_API_KEY = [Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY', 'User')
}
if (-not $env:DEEPSEEK_API_KEY) {
    throw 'Set DEEPSEEK_API_KEY in your local environment before starting the backend.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    & $Python -B -m agentic_workflow --host 127.0.0.1 --port $Port --model-provider deepseek
    if ($LASTEXITCODE -ne 0) { throw "Backend exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$env:ATLAS_BACKEND = 'demo'
$env:ATLAS_SANDBOX = 'local'
$env:ATLAS_ALLOW_TRUSTED_LOCAL = 'true'
& .\.venv\Scripts\atlas.exe serve

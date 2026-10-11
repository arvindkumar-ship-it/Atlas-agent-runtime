$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required' }
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed' }
& .\.venv\Scripts\python.exe -m pip install --no-deps -e .
if ($LASTEXITCODE -ne 0) { throw 'Project install failed' }
if (Get-Command npm -ErrorAction SilentlyContinue) {
    Push-Location frontend
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Console build failed' }
    } finally { Pop-Location }
}
Write-Host 'Setup complete. Run: .\.venv\Scripts\atlas.exe demo auth --trusted-local'

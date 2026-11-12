# Books in Books: set up on first run, then start the app.
#
#   .\start.ps1            use the small spaCy model (12 MB, quick to install)
#   .\start.ps1 -Full      use the transformer model (about 500 MB, most accurate)
#
# If PowerShell refuses to run scripts, start it once with:
#   powershell -ExecutionPolicy Bypass -File .\start.ps1

param([switch]$Full)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$model = if ($Full) { "en_core_web_trf" } else { "en_core_web_sm" }

if (-not (Test-Path $python)) {
    Write-Host "First run: creating the Python environment..."
    python -m venv .venv
    if (-not (Test-Path $python)) { throw "Could not create .venv. Is Python 3.10+ installed?" }
    & $python -m pip install --quiet --upgrade pip
    & $python -m pip install --quiet -r requirements.txt
}

# Install the chosen language model once
& $python -c "import spacy, sys; sys.exit(0 if spacy.util.is_package('$model') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the spaCy model $model..."
    & $python -m spacy download $model
}
$env:BOOKS_SPACY_MODEL = $model

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "Note: ANTHROPIC_API_KEY is not set. The Judge is off and matching is by title only."
}

Write-Host "Starting Books in Books on http://localhost:5000 (Ctrl+C to stop)"
& $python app.py

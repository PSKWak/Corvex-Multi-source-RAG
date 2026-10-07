# Bootstrap on Windows PowerShell.
#   .\scripts\bootstrap.ps1                 # offline path: local + hashing + mock
#   .\scripts\bootstrap.ps1 -Bge -Elastic   # full path
param(
  [switch]$Bge,
  [switch]$Elastic,
  [switch]$Optional   # install all of requirements-optional.txt
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
if ($Optional -or $Bge -or $Elastic) {
  .\.venv\Scripts\pip.exe install -r requirements-optional.txt
}

if (-not (Test-Path .env) -and (Test-Path .env.example)) { Copy-Item .env.example .env }

if ($Elastic) {
  docker compose up -d
  Write-Host "Waiting for Elasticsearch..."
  do { Start-Sleep 3 } until (
    (try { (Invoke-WebRequest -UseBasicParsing http://localhost:9200/_cluster/health).StatusCode -eq 200 } catch { $false })
  )
  # flip config to elastic
  (Get-Content config.yaml) -replace 'backend: local', 'backend: elastic' | Set-Content config.yaml
}
if ($Bge) {
  (Get-Content config.yaml) -replace 'provider: hashing', 'provider: bge'      | Set-Content config.yaml
  (Get-Content config.yaml) -replace 'provider: bge$',    'provider: bge'      | Set-Content config.yaml  # no-op guard
}

.\.venv\Scripts\python.exe -m corvex_rag.cli ingest
.\.venv\Scripts\python.exe -m corvex_rag.cli ask "What is the default retention period for completed jobs?"

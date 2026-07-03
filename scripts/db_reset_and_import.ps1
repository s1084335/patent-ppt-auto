[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,

    [switch]$ConfirmReset,

    [string]$Database = "patent_ppt",
    [string]$User = "postgres",
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$PgBin = "D:\PostgreSQL\18\bin",
    [string]$SchemaFile = "sql\005_six_table_schema.sql",
    [string]$UvCacheDir = ".uv-cache"
)

$ErrorActionPreference = "Stop"

if (-not $ConfirmReset) {
    throw "This is destructive. Re-run with -ConfirmReset after confirming a backup is acceptable."
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot

$resolvedInputFile = (Resolve-Path -LiteralPath $InputFile).Path
$resolvedSchemaFile = (Resolve-Path -LiteralPath $SchemaFile).Path

$psql = Join-Path $PgBin "psql.exe"
if (-not (Test-Path -LiteralPath $psql)) {
    throw "psql.exe not found: $psql"
}

if (-not $env:PGPASSWORD) {
    $userPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "User")
    if ($userPassword) {
        $env:PGPASSWORD = $userPassword
    }
}

Write-Host "Step 1/3: creating backup before reset."
$backupScript = Join-Path $PSScriptRoot "db_backup.ps1"
& $backupScript `
    -Database $Database `
    -User $User `
    -HostName $HostName `
    -Port $Port `
    -PgBin $PgBin

if ($LASTEXITCODE -ne 0) {
    throw "Backup step failed with exit code $LASTEXITCODE"
}

Write-Host "Step 2/3: applying schema. This clears existing imported data."
& $psql `
    -w `
    -h $HostName `
    -p $Port `
    -U $User `
    -d $Database `
    -f $resolvedSchemaFile

if ($LASTEXITCODE -ne 0) {
    throw "Schema reset failed with exit code $LASTEXITCODE"
}

Write-Host "Step 3/3: importing WIPS file."
$env:UV_CACHE_DIR = $UvCacheDir
python -m uv run python -m backend.app.importers.wips_importer $resolvedInputFile

if ($LASTEXITCODE -ne 0) {
    throw "Import failed with exit code $LASTEXITCODE"
}

Write-Host "Reset and import completed."

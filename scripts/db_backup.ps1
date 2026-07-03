[CmdletBinding()]
param(
    [string]$Database = "patent_ppt",
    [string]$User = "postgres",
    [string]$HostName = "localhost",
    [int]$Port = 5432,
    [string]$PgBin = "D:\PostgreSQL\18\bin",
    [string]$BackupDir = "backups"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ResolvedBackupDir = Join-Path $ProjectRoot $BackupDir
New-Item -ItemType Directory -Path $ResolvedBackupDir -Force | Out-Null

$pgDump = Join-Path $PgBin "pg_dump.exe"
if (-not (Test-Path -LiteralPath $pgDump)) {
    throw "pg_dump.exe not found: $pgDump"
}

if (-not $env:PGPASSWORD) {
    $userPassword = [Environment]::GetEnvironmentVariable("PGPASSWORD", "User")
    if ($userPassword) {
        $env:PGPASSWORD = $userPassword
    }
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = Join-Path $ResolvedBackupDir "$Database`_$timestamp.dump"

Write-Host "Creating backup: $backupFile"

& $pgDump `
    -w `
    -h $HostName `
    -p $Port `
    -U $User `
    -d $Database `
    -F c `
    -f $backupFile

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE"
}

Write-Host "Backup completed: $backupFile"
Write-Output $backupFile

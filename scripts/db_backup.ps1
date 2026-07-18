[CmdletBinding()]
param(
    [string]$Database = "patent_ppt",
    [string]$User = "postgres",
    [string]$HostName = "localhost",
    [int]$Port = 5433,
    [string]$PgBin = "D:\PostgreSQL\18\bin",
    [string]$BackupDir = "backups",
    [switch]$SchemaOnly
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
$backupKind = if ($SchemaOnly) { "schema" } else { "full" }
$backupFile = Join-Path $ResolvedBackupDir "$Database`_$backupKind`_$timestamp.dump"

Write-Host "Creating $backupKind backup: $backupFile"

$dumpArgs = @(
    "-w",
    "-h", $HostName,
    "-p", $Port,
    "-U", $User,
    "-d", $Database,
    "-F", "c",
    "-f", $backupFile
)

if ($SchemaOnly) {
    $dumpArgs += "--schema-only"
}

& $pgDump @dumpArgs

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE"
}

Write-Host "$backupKind backup completed: $backupFile"
Write-Output $backupFile

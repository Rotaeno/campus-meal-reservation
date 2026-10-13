$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path $PSScriptRoot -Parent
$script:RuntimeRoot = if ($env:SKY_RUNTIME_DIR) { $env:SKY_RUNTIME_DIR } else { Join-Path (Split-Path $script:ProjectRoot -Parent) '.runtime' }
$script:LocalRoot = Join-Path $script:ProjectRoot '.local'
New-Item -ItemType Directory -Path $script:LocalRoot -Force | Out-Null
function Get-LocalTools {
    $script:JavaHome = (Get-ChildItem $script:RuntimeRoot -Directory -Filter 'jdk1.8*' | Select-Object -First 1).FullName
    $script:MavenHome = (Get-ChildItem $script:RuntimeRoot -Directory -Filter 'apache-maven-*' | Select-Object -First 1).FullName
    $script:MysqlHome = (Get-ChildItem $script:RuntimeRoot -Directory -Filter 'mysql-*-winx64' | Select-Object -First 1).FullName
    $script:RedisHome = (Get-ChildItem (Join-Path $script:RuntimeRoot 'redis') -Directory | Select-Object -First 1).FullName
    if (!$script:JavaHome -or !$script:MavenHome -or !$script:MysqlHome -or !$script:RedisHome) { throw 'Run scripts/bootstrap.ps1 first.' }
    $env:JAVA_HOME = $script:JavaHome
    $env:PATH = "$script:JavaHome\bin;$env:PATH"
}
function Set-LocalEnvironment {
    $taskFile = Join-Path $script:RuntimeRoot 'credentials.json'
    $taskSecrets = Get-Content -LiteralPath $taskFile -Raw | ConvertFrom-Json
    foreach ($taskKey in @('adminJwtSecret','userJwtSecret')) {
        if (!$taskSecrets.PSObject.Properties[$taskKey]) { $taskSecrets | Add-Member -NotePropertyName $taskKey -NotePropertyValue ([Guid]::NewGuid().ToString('N')) }
    }
    $taskSecrets | ConvertTo-Json | Set-Content -LiteralPath $taskFile -Encoding UTF8
    $env:SKY_DB_PASSWORD = $taskSecrets.mysqlPassword
    $env:SKY_REDIS_PASSWORD = $taskSecrets.redisPassword
    $env:SKY_ADMIN_JWT_SECRET = $taskSecrets.adminJwtSecret
    $env:SKY_USER_JWT_SECRET = $taskSecrets.userJwtSecret
    $env:SKY_UPLOAD_DIR = Join-Path $script:RuntimeRoot 'uploads'
    New-Item -ItemType Directory -Path $env:SKY_UPLOAD_DIR -Force | Out-Null
    return $taskSecrets
}
function Test-LocalPort([int]$Port) {
    $taskClient = New-Object Net.Sockets.TcpClient
    try {
        $taskConnection = $taskClient.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (!$taskConnection.AsyncWaitHandle.WaitOne(300)) { return $false }
        $taskClient.EndConnect($taskConnection)
        return $true
    } catch { return $false } finally { $taskClient.Close() }
}
function Wait-LocalPort([int]$Port, [int]$Seconds = 30) {
    $taskDeadline = (Get-Date).AddSeconds($Seconds)
    while (!(Test-LocalPort $Port)) {
        if ((Get-Date) -gt $taskDeadline) { throw "Port $Port did not become ready. See .local logs." }
        Start-Sleep -Milliseconds 500
    }
}

param([switch]$All)
. "$PSScriptRoot\common.ps1"
Get-LocalTools
$taskSecrets = Set-LocalEnvironment
$taskNginxRoot = Join-Path $script:ProjectRoot 'nginx-1.20.2'
if (Test-Path "$script:LocalRoot\nginx.pid") {
    & "$taskNginxRoot\nginx.exe" -p "$taskNginxRoot/" -c "$script:LocalRoot\nginx.conf" -s quit
}
if (Test-Path "$script:LocalRoot\backend.pid") {
    $taskBackendId = [int](Get-Content "$script:LocalRoot\backend.pid")
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskBackendId"
    if ($taskProcess -and $taskProcess.ExecutablePath -eq "$script:JavaHome\bin\java.exe" -and $taskProcess.CommandLine.Contains('sky-server-1.0-SNAPSHOT.jar')) { Stop-Process -Id $taskBackendId }
}
if ($All) {
    $env:REDISCLI_AUTH = $taskSecrets.redisPassword
    try { & "$script:RedisHome\redis-cli.exe" -h 127.0.0.1 -p 6379 SHUTDOWN SAVE } finally { Remove-Item Env:\REDISCLI_AUTH -ErrorAction SilentlyContinue }
    $taskRootConfig = Join-Path $script:LocalRoot 'mysql-root.cnf'
    [IO.File]::WriteAllText($taskRootConfig,"[client]`nhost=127.0.0.1`nport=3306`nuser=root`npassword=$($taskSecrets.mysqlRootPassword)`n",(New-Object Text.UTF8Encoding($false)))
    try { & "$script:MysqlHome\bin\mysqladmin.exe" "--defaults-extra-file=$taskRootConfig" shutdown } finally { Remove-Item -LiteralPath $taskRootConfig }
}
Write-Host 'Local services stopped; database data is preserved.'

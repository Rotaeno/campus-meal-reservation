. "$PSScriptRoot\common.ps1"
New-Item -ItemType Directory -Path $script:RuntimeRoot -Force | Out-Null
function Get-Archive([string]$Name,[string]$Url,[string]$Hash,[string]$Destination) {
    $taskArchive = Join-Path $script:RuntimeRoot $Name
    if (!(Test-Path -LiteralPath $taskArchive)) {
        & curl.exe -f -L --retry 2 --connect-timeout 20 --max-time 900 -o $taskArchive $Url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $Name" }
    }
    if ($Hash -and (Get-FileHash $taskArchive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash.ToLowerInvariant()) { throw "Checksum mismatch: $Name" }
    Expand-Archive -LiteralPath $taskArchive -DestinationPath $Destination -Force
}
if (!(Get-ChildItem $script:RuntimeRoot -Directory -Filter 'jdk1.8*')) {
    $taskJavaHash = (Invoke-WebRequest -UseBasicParsing 'https://corretto.aws/downloads/latest_sha256/amazon-corretto-8-x64-windows-jdk.zip').Content.Trim()
    Get-Archive 'corretto-jdk8.zip' 'https://corretto.aws/downloads/latest/amazon-corretto-8-x64-windows-jdk.zip' $taskJavaHash $script:RuntimeRoot
}
if (!(Test-Path "$script:RuntimeRoot\apache-maven-3.9.12")) {
    Get-Archive 'maven.zip' 'https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.12/apache-maven-3.9.12-bin.zip' '' $script:RuntimeRoot
    $taskMavenHash = (Invoke-WebRequest -UseBasicParsing 'https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.12/apache-maven-3.9.12-bin.zip.sha512').Content.Trim()
    if ((Get-FileHash "$script:RuntimeRoot\maven.zip" -Algorithm SHA512).Hash.ToLowerInvariant() -ne $taskMavenHash.ToLowerInvariant()) { throw 'Maven checksum mismatch' }
}
if (!(Test-Path "$script:RuntimeRoot\mysql-8.0.46-winx64")) {
    Get-Archive 'mysql.zip' 'https://cdn.mysql.com/Downloads/MySQL-8.0/mysql-8.0.46-winx64.zip' '28e9eda019d88eff4478d811ea2110b83f02a3966be157fe91cc55def3ab0d4d' $script:RuntimeRoot
}
if (!(Test-Path "$script:RuntimeRoot\redis\Redis-7.4.3-Windows-x64-cygwin")) {
    Get-Archive 'redis.zip' 'https://github.com/redis-windows/redis-windows/releases/download/7.4.3/Redis-7.4.3-Windows-x64-cygwin.zip' 'f6fe3dbbea180672fe08cba80759168ea68a51cd7ee4f097e1b8905a053d0ad1' "$script:RuntimeRoot\redis"
}
Get-LocalTools
$taskCredentialsFile = Join-Path $script:RuntimeRoot 'credentials.json'
if (!(Test-Path -LiteralPath $taskCredentialsFile)) {
    if (Test-LocalPort 3306) { throw 'Port 3306 is already used. Bootstrap will not change an existing database.' }
    if (!(Test-Path "$script:RuntimeRoot\mysql-data")) {
        & "$script:MysqlHome\bin\mysqld.exe" --no-defaults --initialize-insecure "--basedir=$script:MysqlHome" "--datadir=$script:RuntimeRoot\mysql-data" --console
        if ($LASTEXITCODE -ne 0) { throw 'MySQL initialization failed.' }
    }
    $taskMysql = Start-Process "$script:MysqlHome\bin\mysqld.exe" -ArgumentList @('--no-defaults',"--basedir=`"$script:MysqlHome`"","--datadir=`"$script:RuntimeRoot\mysql-data`"",'--bind-address=127.0.0.1','--port=3306','--mysqlx=0','--console') -WindowStyle Hidden -RedirectStandardOutput "$script:LocalRoot\mysql.stdout.log" -RedirectStandardError "$script:LocalRoot\mysql.stderr.log" -PassThru
    $taskMysql.Id | Set-Content "$script:LocalRoot\mysql.pid"
    Wait-LocalPort 3306
    $taskSecrets = [pscustomobject]@{
        mysqlRootPassword = [Guid]::NewGuid().ToString('N')
        mysqlPassword = [Guid]::NewGuid().ToString('N')
        redisPassword = [Guid]::NewGuid().ToString('N')
    }
    $taskSql = @"
ALTER USER 'root'@'localhost' IDENTIFIED BY '$($taskSecrets.mysqlRootPassword)';
CREATE DATABASE IF NOT EXISTS sky_take_out CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sky_local'@'localhost' IDENTIFIED BY '$($taskSecrets.mysqlPassword)';
GRANT ALL PRIVILEGES ON sky_take_out.* TO 'sky_local'@'localhost';
"@
    $taskBootstrap = Join-Path $script:LocalRoot 'mysql-bootstrap.sql'
    [IO.File]::WriteAllText($taskBootstrap,$taskSql,(New-Object Text.UTF8Encoding($false)))
    try {
        & "$script:MysqlHome\bin\mysql.exe" --no-defaults --host=127.0.0.1 --port=3306 --user=root --execute="source $($taskBootstrap.Replace('\','/'))"
        if ($LASTEXITCODE -ne 0) { throw 'MySQL account setup failed.' }
        $taskSecrets | ConvertTo-Json | Set-Content $taskCredentialsFile -Encoding UTF8
    } finally { Remove-Item -LiteralPath $taskBootstrap }
}
$taskSecrets = Set-LocalEnvironment
$taskClientConfig = "[client]`nhost=127.0.0.1`nport=3306`nuser=sky_local`npassword=$($taskSecrets.mysqlPassword)`ndefault-character-set=utf8mb4`n"
[IO.File]::WriteAllText((Join-Path $script:RuntimeRoot 'mysql-client.cnf'),$taskClientConfig,(New-Object Text.UTF8Encoding($false)))
$taskRedisData = Join-Path $script:RuntimeRoot 'redis-data'
New-Item -ItemType Directory -Path $taskRedisData -Force | Out-Null
$taskCygwinData = '/cygdrive/' + $taskRedisData.Substring(0,1).ToLowerInvariant() + '/' + $taskRedisData.Substring(3).Replace('\','/')
$taskRedisConfig = "bind 127.0.0.1`nprotected-mode yes`nport 6379`nrequirepass $($taskSecrets.redisPassword)`ndir $taskCygwinData`nmaxclients 3000`nsave 60 1`nappendonly no`n"
[IO.File]::WriteAllText((Join-Path $script:RuntimeRoot 'redis.conf'),$taskRedisConfig,(New-Object Text.UTF8Encoding($false)))
if (!(Test-LocalPort 3306)) {
    $taskMysql = Start-Process "$script:MysqlHome\bin\mysqld.exe" -ArgumentList @('--no-defaults',"--basedir=`"$script:MysqlHome`"","--datadir=`"$script:RuntimeRoot\mysql-data`"",'--bind-address=127.0.0.1','--port=3306','--mysqlx=0','--console') -WindowStyle Hidden -RedirectStandardOutput "$script:LocalRoot\mysql.stdout.log" -RedirectStandardError "$script:LocalRoot\mysql.stderr.log" -PassThru
    $taskMysql.Id | Set-Content "$script:LocalRoot\mysql.pid"
    Wait-LocalPort 3306
}
$taskTableCount = & "$script:MysqlHome\bin\mysql.exe" "--defaults-extra-file=$script:RuntimeRoot\mysql-client.cnf" -N -B --execute="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='sky_take_out'"
if ($LASTEXITCODE -ne 0) { throw 'MySQL connection verification failed.' }
if ([int]$taskTableCount -eq 0) {
    $taskSchema = Join-Path $PSScriptRoot 'sql\000-initial-schema.sql'
    & "$script:MysqlHome\bin\mysql.exe" "--defaults-extra-file=$script:RuntimeRoot\mysql-client.cnf" --execute="source $($taskSchema.Replace('\','/'))"
    if ($LASTEXITCODE -ne 0) { throw 'Schema import failed.' }
} elseif ([int]$taskTableCount -notin @(11,14)) { throw 'Expected 11 baseline tables or 14 tables after Flyway migration. Existing data was preserved.' }
$taskMigration = Join-Path $PSScriptRoot 'sql\001-demo-admin-password.sql'
& "$script:MysqlHome\bin\mysql.exe" "--defaults-extra-file=$script:RuntimeRoot\mysql-client.cnf" --database=sky_take_out --execute="source $($taskMigration.Replace('\','/'))"
if ($LASTEXITCODE -ne 0) { throw 'Demo seed migration failed.' }
Write-Host 'Portable tools and database are ready. Existing tables and data were preserved.'
Write-Host 'Next: powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Build'

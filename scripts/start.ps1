param([switch]$Build)
. "$PSScriptRoot\common.ps1"
Get-LocalTools
if (!(Test-Path "$script:LocalRoot\frontend\index.html")) {
    throw 'Build the Vue frontend first: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-front.ps1 -Install'
}
$taskSecrets = Set-LocalEnvironment
$taskJar = Join-Path $script:ProjectRoot 'sky-take-out\sky-server\target\sky-server-1.0-SNAPSHOT.jar'
if ($Build -or !(Test-Path -LiteralPath $taskJar)) { & "$PSScriptRoot\build.ps1" }
if (!(Test-LocalPort 3306)) {
    $taskMysql = Start-Process "$script:MysqlHome\bin\mysqld.exe" -ArgumentList @('--no-defaults',"--basedir=`"$script:MysqlHome`"","--datadir=`"$script:RuntimeRoot\mysql-data`"",'--bind-address=127.0.0.1','--port=3306','--mysqlx=0','--console') -WindowStyle Hidden -RedirectStandardOutput "$script:LocalRoot\mysql.stdout.log" -RedirectStandardError "$script:LocalRoot\mysql.stderr.log" -PassThru
    $taskMysql.Id | Set-Content "$script:LocalRoot\mysql.pid"
}
Wait-LocalPort 3306
& "$script:MysqlHome\bin\mysql.exe" "--defaults-extra-file=$script:RuntimeRoot\mysql-client.cnf" --database=sky_take_out --execute='SELECT 1' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'MySQL on port 3306 does not match this local configuration.' }
if (!(Test-LocalPort 6379)) {
    $taskRedisConfig = '/cygdrive/' + $script:RuntimeRoot.Substring(0,1).ToLowerInvariant() + '/' + $script:RuntimeRoot.Substring(3).Replace('\','/') + '/redis.conf'
    $taskRedis = Start-Process "$script:RedisHome\redis-server.exe" -ArgumentList @($taskRedisConfig) -WorkingDirectory $script:RedisHome -WindowStyle Hidden -RedirectStandardOutput "$script:LocalRoot\redis.stdout.log" -RedirectStandardError "$script:LocalRoot\redis.stderr.log" -PassThru
    $taskRedis.Id | Set-Content "$script:LocalRoot\redis.pid"
}
Wait-LocalPort 6379
$env:REDISCLI_AUTH = $taskSecrets.redisPassword
try {
    $taskPing = & "$script:RedisHome\redis-cli.exe" -h 127.0.0.1 -p 6379 PING
    if ($taskPing -ne 'PONG') { throw 'Redis authentication failed.' }
} finally { Remove-Item Env:\REDISCLI_AUTH -ErrorAction SilentlyContinue }
if (!(Test-LocalPort 8080)) {
    $taskBackend = Start-Process "$script:JavaHome\bin\java.exe" -ArgumentList @('-Dfile.encoding=UTF-8','-jar',"`"$taskJar`"",'--spring.profiles.active=local','--debug=false') -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -RedirectStandardOutput "$script:LocalRoot\backend.stdout.log" -RedirectStandardError "$script:LocalRoot\backend.stderr.log" -PassThru
    $taskBackend.Id | Set-Content "$script:LocalRoot\backend.pid"
}
Wait-LocalPort 8080 45
$taskNginxRoot = Join-Path $script:ProjectRoot 'nginx-1.20.2'
New-Item -ItemType Directory -Path "$taskNginxRoot\temp" -Force | Out-Null
New-Item -ItemType Directory -Path "$taskNginxRoot\logs" -Force | Out-Null
$taskNginxRootUrl = $taskNginxRoot.Replace('\','/')
$taskLocalUrl = $script:LocalRoot.Replace('\','/')
$taskUploadUrl = $env:SKY_UPLOAD_DIR.Replace('\','/')
$taskFrontendRoot = "$taskLocalUrl/frontend"
$taskConfig = @"
worker_processes 1;
pid "$taskLocalUrl/nginx.pid";
error_log "$taskLocalUrl/nginx.error.log";
events { worker_connections 1024; }
http {
  include "$taskNginxRootUrl/conf/mime.types";
  default_type application/octet-stream;
  charset utf-8;
  access_log "$taskLocalUrl/nginx.access.log";
  map `$http_upgrade `$connection_upgrade { default upgrade; '' close; }
  server {
    listen 127.0.0.1:8081;
    server_name localhost;
    root "$taskFrontendRoot";
    location / { try_files `$uri `$uri/ /index.html; }
    location /api/ { proxy_pass http://127.0.0.1:8080/admin/; }
    location /user/ { proxy_pass http://127.0.0.1:8080/user/; }
    location /local-files/ { alias "$taskUploadUrl/"; }
    location /ws/ {
      proxy_pass http://127.0.0.1:8080/ws/;
      proxy_http_version 1.1;
      proxy_read_timeout 3600s;
      proxy_set_header Upgrade `$http_upgrade;
      proxy_set_header Connection `$connection_upgrade;
    }
  }
}
"@
$taskConfigPath = Join-Path $script:LocalRoot 'nginx.conf'
[IO.File]::WriteAllText($taskConfigPath,$taskConfig,(New-Object Text.UTF8Encoding($false)))
& "$taskNginxRoot\nginx.exe" -t -p "$taskNginxRoot/" -c $taskConfigPath
if ($LASTEXITCODE -ne 0) { throw 'Nginx configuration validation failed.' }
if (!(Test-LocalPort 8081)) {
    Start-Process "$taskNginxRoot\nginx.exe" -ArgumentList @('-p',"`"$taskNginxRoot/`"",'-c',"`"$taskConfigPath`"") -WorkingDirectory $taskNginxRoot -WindowStyle Hidden
}
Wait-LocalPort 8081
$taskStatus = Invoke-RestMethod 'http://127.0.0.1:8080/user/shop/status'
if ($taskStatus.code -ne 1) { throw 'Backend readiness check failed.' }
Write-Host 'Admin: http://127.0.0.1:8081   admin / 123456'
Write-Host 'API docs: http://127.0.0.1:8080/doc.html'
Write-Host 'Profile: local. WeChat login/payment/refund, OSS and delivery distance are local simulations.'

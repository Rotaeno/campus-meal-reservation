param([switch]$Install)
. "$PSScriptRoot\common.ps1"
$taskPreviousNodeOptions = $env:NODE_OPTIONS
Push-Location (Join-Path $script:ProjectRoot 'project-rjwm-admin-vue-ts')
try {
    if ($Install -or !(Test-Path 'node_modules\@vue\cli-service\bin\vue-cli-service.js')) {
        $taskSavedErrorAction = $ErrorActionPreference
        try {
            # Windows PowerShell redirects native warnings into ErrorRecords; judge npm by its exit code.
            $ErrorActionPreference = 'Continue'
            & npm.cmd ci --ignore-scripts --legacy-peer-deps --no-audit --no-fund --registry=https://registry.npmjs.org
            $taskExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $taskSavedErrorAction }
        if ($taskExit -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    $env:NODE_OPTIONS = '--openssl-legacy-provider --max-old-space-size=4096'
    $taskSavedErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & npm.cmd run build -- --dest "$script:LocalRoot\frontend"
        $taskExit = $LASTEXITCODE
    } finally { $ErrorActionPreference = $taskSavedErrorAction }
    if ($taskExit -ne 0) { throw 'Frontend build failed.' }
} finally {
    $env:NODE_OPTIONS = $taskPreviousNodeOptions
    Pop-Location
}
Write-Host 'Frontend built in .local/frontend. Restart Nginx through scripts/stop.ps1 and scripts/start.ps1.'

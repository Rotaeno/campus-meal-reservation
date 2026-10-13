param([switch]$SkipTests)
. "$PSScriptRoot\common.ps1"
Get-LocalTools
Push-Location (Join-Path $script:ProjectRoot 'sky-take-out')
try {
    $taskArguments = @('-B','-ntp',"-Dmaven.repo.local=$script:RuntimeRoot\m2",'package')
    if ($SkipTests) { $taskArguments += '-DskipTests' }
    & "$script:MavenHome\bin\mvn.cmd" @taskArguments
    if ($LASTEXITCODE -ne 0) { throw 'Maven build failed.' }
} finally { Pop-Location }

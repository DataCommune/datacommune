# Playwright owns this launcher; the invocation owns its exact Node child.
[CmdletBinding()]
param([Parameter(Mandatory)][string]$RunManifest,
    [Parameter(Mandatory)][ValidateSet('app','source')][string]$Kind,
    [string]$HostAddress = '127.0.0.1')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'test-resources.ps1')
$run = Get-TestRun $RunManifest
if ($run.status -ne 'active' -or -not $run.web) { throw 'Active web invocation required' }
$node = (Get-Command node -ErrorAction Stop).Source
$frontend = Join-Path $run.repo_root 'frontend'
$logs = Split-Path -Parent $RunManifest
$launchId = [guid]::NewGuid().ToString('N')
$savedEnvironment = Save-TestEnvironment @('DPE_SOURCE_PORT')
if ($Kind -eq 'source') {
    $arguments = @('--experimental-strip-types', (Join-Path $frontend 'tests/e2e/fixtures/dpe-source-server.ts'))
    $env:DPE_SOURCE_PORT = [string]$run.web.source_port
}
else {
    $arguments = @((Join-Path $frontend 'node_modules/vite/bin/vite.js'),'--host',$HostAddress,
        '--port',[string]$run.web.app_port,'--strictPort')
}
$process = $null
$identity = $null
$registered = $false
$result = 1
$cleanupFailed = $false
try {
    # Start-Process does not quote its argument array: quote each path/argument explicitly.
    $quoted = @($arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' })
    $process = Start-Process -FilePath $node -ArgumentList $quoted -WorkingDirectory $frontend `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "$Kind-$launchId.out.log") `
        -RedirectStandardError (Join-Path $logs "$Kind-$launchId.err.log")
    $native = Get-CimInstance Win32_Process -Filter "ProcessId=$($process.Id)" -ErrorAction Stop
    if (-not $native -or $native.ParentProcessId -ne $PID -or
        -not $native.CommandLine.Contains($arguments[0 + [int]($Kind -eq 'source')], [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Web launch identity unavailable'
    }
    $identity = [pscustomobject]@{pid=$process.Id;
        creation_date=$native.CreationDate.ToUniversalTime().Ticks.ToString();command_line=$native.CommandLine}
    Register-TestProcess -RunManifest $RunManifest -ProcessId $process.Id -CommandLine $native.CommandLine
    $registered = $true
    $process.WaitForExit()
    $result = $process.ExitCode
}
catch { Write-Error $_ -ErrorAction Continue }
finally {
    try {
        if ($registered) { Stop-TestRun -RunManifest $RunManifest -ProcessId $process.Id }
        elseif ($identity) { Stop-OwnedTestProcess -Identity $identity }
        elseif ($process -and -not $process.HasExited) {
            # Do not guess an identity after an inspection failure.
            throw 'Created web process could not be identified; cleanup is unverified'
        }
    }
    catch { $cleanupFailed = $true; Write-Error $_ -ErrorAction Continue }
    finally { Restore-TestEnvironment $savedEnvironment }
}
if ($cleanupFailed -and $result -eq 0) { $result = 1 }
exit $result

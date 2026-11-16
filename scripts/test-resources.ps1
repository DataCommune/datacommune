# Invocation ownership shared by the versioned runners. No host-wide discovery.
#Requires -Version 7
Set-StrictMode -Version Latest

function Get-TestRun {
    param([Parameter(Mandatory)][string]$RunManifest, [string]$RepoRoot)
    if (-not (Test-Path -LiteralPath $RunManifest -PathType Leaf)) { throw 'Test ownership manifest is missing' }
    $run = Get-Content -LiteralPath $RunManifest -Raw | ConvertFrom-Json
    if ($run.version -ne 1 -or $run.run_id -notmatch '^[a-f0-9]{32}$' -or $run.status -notin @('active','stopped')) {
        throw 'Invalid test run ownership'
    }
    $root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
    if ($RepoRoot) { $root = (Resolve-Path -LiteralPath $RepoRoot).Path }
    if ($run.repo_root -ne $root) { throw 'Test run belongs to another checkout' }
    foreach ($project in $run.projects) {
        if ($project.name -notmatch "^dc-verify-$($run.run_id)-[a-f0-9]{10}$" -or
            $project.compose_file -ne (Join-Path $root 'api/docker-compose.contract-tests.yml')) {
            throw 'Project outside owned invocation'
        }
    }
    foreach ($process in $run.processes) {
        if ($process.pid -le 0 -or -not $process.creation_date -or -not $process.command_line) {
            throw 'Incomplete process ownership'
        }
    }
    return $run
}

function Update-TestRun {
    param([Parameter(Mandatory)][string]$RunManifest, [Parameter(Mandatory)][scriptblock]$Update)
    $lock = $null
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while (-not $lock) {
        try { $lock = [IO.File]::Open("$RunManifest.lock", 'OpenOrCreate', 'ReadWrite', 'None') }
        catch [IO.IOException] {
            if ([DateTime]::UtcNow -gt $deadline) { throw 'Test registry is busy' }
            Start-Sleep -Milliseconds 25
        }
    }
    try {
        $run = Get-TestRun -RunManifest $RunManifest
        & $Update $run
        $temporary = "$RunManifest.$([guid]::NewGuid().ToString('N')).tmp"
        try {
            $run | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
            [IO.File]::Move($temporary, $RunManifest, $true)
        }
        finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
    }
    finally { $lock.Dispose() }
}

# Registries that own nothing, or whose run stopped cleanly a while ago, are only
# logs and Playwright traces. Failed or still-owning runs are always kept.
function Remove-StaleTestRuns {
    param([int]$StoppedRetentionDays = 7)
    $root = Join-Path ([IO.Path]::GetTempPath()) 'dc-test-runs'
    if (-not (Test-Path -LiteralPath $root -PathType Container)) { return }
    $now = [DateTime]::UtcNow
    foreach ($directory in Get-ChildItem -LiteralPath $root -Directory) {
        if ($directory.Name -notmatch '^[a-f0-9]{32}$') { continue }
        $manifest = Join-Path $directory.FullName 'run.json'
        try {
            $item = Get-Item -LiteralPath $manifest -ErrorAction Stop
            $run = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
            $age = $now - $item.LastWriteTimeUtc
            $ownsNothing = -not @($run.projects | Where-Object { $_ }).Count -and
                -not @($run.processes | Where-Object { $_ }).Count -and -not $run.web
            $stale = ($run.status -eq 'active' -and $ownsNothing -and $age.TotalDays -ge 1) -or
                ($run.status -eq 'stopped' -and $age.TotalDays -ge $StoppedRetentionDays)
            if ($stale) { Remove-Item -LiteralPath $directory.FullName -Recurse -Force -ErrorAction Stop }
        }
        catch { Write-Warning "Kept test run registry $($directory.Name): $($_.Exception.Message)" }
    }
}

function Initialize-TestRun {
    param([Parameter(Mandatory)][string]$RepoRoot, [string]$RunManifest)
    if ($RunManifest) {
        $run = Get-TestRun -RunManifest $RunManifest -RepoRoot $RepoRoot
        if ($run.status -ne 'active') { throw 'Cannot reuse a completed invocation' }
    }
    else {
        Remove-StaleTestRuns
        $id = [guid]::NewGuid().ToString('N')
        $directory = Join-Path ([IO.Path]::GetTempPath()) "dc-test-runs/$id"
        [IO.Directory]::CreateDirectory($directory) | Out-Null
        $RunManifest = Join-Path $directory 'run.json'
        [ordered]@{version=1;run_id=$id;repo_root=(Resolve-Path -LiteralPath $RepoRoot).Path;
            status='active';projects=@();processes=@();web=$null} |
            ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $RunManifest -Encoding utf8
        $run = Get-TestRun -RunManifest $RunManifest -RepoRoot $RepoRoot
    }
    Write-Host "TEST_RUN $($run.run_id) manifest=$RunManifest"
    return $RunManifest
}

function Set-TestRunEnvironment {
    param([Parameter(Mandatory)][string]$RunManifest)
    $run = Get-TestRun $RunManifest
    $env:DC_TEST_RUN_MANIFEST = $RunManifest
    $env:DC_TEST_RUN_ID = $run.run_id
}

function Register-TestProject {
    param([Parameter(Mandatory)][string]$RunManifest,[Parameter(Mandatory)][string]$ProjectName,
        [Parameter(Mandatory)][string]$ComposeFile)
    Update-TestRun $RunManifest {
        param($run)
        if ($run.status -ne 'active' -or $ProjectName -notmatch "^dc-verify-$($run.run_id)-[a-f0-9]{10}$" -or
            $ComposeFile -ne (Join-Path $run.repo_root 'api/docker-compose.contract-tests.yml')) { throw 'Refusing unowned project' }
        if (@($run.projects | Where-Object name -eq $ProjectName).Count) { throw 'Project already registered' }
        $run.projects = @($run.projects) + [pscustomobject]@{name=$ProjectName;compose_file=$ComposeFile;status='active'}
    }
}

function Register-TestProcess {
    param([Parameter(Mandatory)][string]$RunManifest,[Parameter(Mandatory)][int]$ProcessId,[string]$CommandLine,
        [string]$ScopeId = $env:DC_TEST_SCOPE_ID)
    $run = Get-TestRun $RunManifest
    $native = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
    if (-not $native -or $native.ParentProcessId -ne $PID -or
        ($CommandLine -and $native.CommandLine -ne $CommandLine) -or
        -not $native.CommandLine.Contains($run.repo_root, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Process was not launched by this owner in its checkout'
    }
    $observedCommand = $native.CommandLine
    $created = $native.CreationDate.ToUniversalTime().Ticks.ToString()
    Update-TestRun $RunManifest {
        param($state)
        if ($state.status -ne 'active') { throw 'Completed run cannot own a process' }
        $state.processes = @($state.processes) + [pscustomobject]@{pid=$ProcessId;creation_date=$created;
            command_line=$observedCommand;scope_id=$ScopeId;status='active'}
    }
}

function Stop-OwnedTestProcess {
    param([Parameter(Mandatory)]$Identity)
    $native = Get-CimInstance Win32_Process -Filter "ProcessId=$($Identity.pid)" -ErrorAction Stop
    if (-not $native) { return }
    if ($native.CreationDate.ToUniversalTime().Ticks.ToString() -ne $Identity.creation_date -or
        $native.CommandLine -ne $Identity.command_line) { throw 'PID reused or launch identity changed' }
    Stop-Process -Id $Identity.pid -Force -ErrorAction Stop
    # Process termination is asynchronous: allow it a short, bounded delay.
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while (Get-CimInstance Win32_Process -Filter "ProcessId=$($Identity.pid)" -ErrorAction Stop) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Owned process still exists' }
        Start-Sleep -Milliseconds 100
    }
}

# Windows reserves TCP ranges (Hyper-V/WSL) that look free but refuse bind().
function Get-ExcludedTcpPortRanges {
    if (-not (Get-Command netsh -CommandType Application -ErrorAction SilentlyContinue)) { return @() }
    $lines = & netsh int ipv4 show excludedportrange protocol=tcp
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read excluded TCP port ranges' }
    return @($lines | ForEach-Object {
        if ($_ -match '^\s*(\d+)\s+(\d+)') { [pscustomobject]@{start=[int]$Matches[1];end=[int]$Matches[2]} }
    })
}

function Get-TestPortCandidate {
    param([int]$Minimum, [int]$Maximum, [object[]]$Excluded)
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        $port = Get-Random -Minimum $Minimum -Maximum $Maximum
        # Without netsh (Linux) $Excluded arrives as $null, which the pipeline still
        # hands to Where-Object once: skip it before reading .start under StrictMode.
        if (-not @($Excluded | Where-Object { $null -ne $_ -and $port -ge $_.start -and $port -le $_.end }).Count) { return $port }
    }
    throw "No test port outside excluded ranges in $Minimum-$Maximum"
}

function Set-TestRunWeb {
    param([Parameter(Mandatory)][string]$RunManifest)
    Update-TestRun $RunManifest {
        param($run)
        if (-not $run.web) {
            $excluded = Get-ExcludedTcpPortRanges
            $app = Get-TestPortCandidate 15000 25000 $excluded
            $source = Get-TestPortCandidate 25000 35000 $excluded
            foreach ($port in @($app,$source)) {
                if (@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $port).Count) {
                    throw "Test port $port is occupied"
                }
            }
            $run.web = [pscustomobject]@{app_port=$app;source_port=$source}
        }
    }
    $run = Get-TestRun $RunManifest
    $env:E2E_APP_ORIGIN = "http://127.0.0.1:$($run.web.app_port)"
    $env:E2E_SOURCE_ORIGIN = "http://127.0.0.1:$($run.web.source_port)"
    return $run.web
}

function Stop-TestRun {
    param([Parameter(Mandatory)][string]$RunManifest,[string]$ProjectName,[int]$ProcessId,[string]$ScopeId)
    $script:TestCleanupFailures = @()
    Update-TestRun $RunManifest {
        param($run)
        if ($ProjectName -and -not @($run.projects | Where-Object name -eq $ProjectName).Count) { throw 'Unregistered project' }
        if ($ProcessId -and -not @($run.processes | Where-Object pid -eq $ProcessId).Count) { throw 'Unregistered process' }
        $failures = @()
        if (-not $ProcessId -and -not $ScopeId) {
            foreach ($project in @($run.projects | Where-Object { $_.status -ne 'stopped' -and (-not $ProjectName -or $_.name -eq $ProjectName) })) {
                try {
                    $containers = @(& docker compose -f $project.compose_file -p $project.name ps -a -q)
                    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect owned compose project' }
                    foreach ($container in $containers) {
                        $labelsJson = & docker inspect --format '{{json .Config.Labels}}' $container
                        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect owned container labels' }
                        $labels = $labelsJson | ConvertFrom-Json
                        if ($labels.'com.docker.compose.project' -ne $project.name -or
                            $labels.'com.docker.compose.project.config_files' -ne $project.compose_file) {
                            throw 'Container identity differs from owned project'
                        }
                    }
                    & docker compose -f $project.compose_file -p $project.name down -v --remove-orphans
                    if ($LASTEXITCODE -ne 0) { throw 'Owned compose cleanup failed' }
                    $remaining = @(& docker compose -f $project.compose_file -p $project.name ps -a -q)
                    if ($LASTEXITCODE -ne 0 -or $remaining.Count) { throw 'Owned compose cleanup could not be verified' }
                    $project.status = 'stopped'
                }
                catch { $failures += "$($project.name): $($_.Exception.Message)" }
            }
        }
        if (-not $ProjectName) {
            foreach ($process in @($run.processes | Where-Object { $_.status -ne 'stopped' -and
                    (-not $ProcessId -or $_.pid -eq $ProcessId) -and (-not $ScopeId -or $_.scope_id -eq $ScopeId) })) {
                try {
                    Stop-OwnedTestProcess -Identity $process
                    $process.status = 'stopped'
                }
                catch { $failures += "Process $($process.pid): $($_.Exception.Message)" }
            }
        }
        if (-not $ProjectName -and -not $ProcessId -and -not $ScopeId -and -not $failures.Count) { $run.status = 'stopped' }
        $script:TestCleanupFailures = $failures
    }
    if ($script:TestCleanupFailures.Count) { throw ($script:TestCleanupFailures -join ' ; ') }
}

function Save-TestEnvironment {
    param([string[]]$Names)
    $saved = @{}
    foreach ($name in $Names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name) }
    return $saved
}

function Restore-TestEnvironment {
    param([hashtable]$Saved)
    foreach ($name in $Saved.Keys) { [Environment]::SetEnvironmentVariable($name, $Saved[$name]) }
}

<#
.SYNOPSIS
    Behavioral resource ownership checks; every service/host command is simulated.
#>
[CmdletBinding()]
param(
    [switch]$Baseline,
    [string]$WorkerCase,
    [string]$TargetScript,
    [string]$CaseDirectory,
    [string]$HelperScript
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path

if ($WorkerCase) {
    # Registries of simulated runs stay in the case directory, never in the host TEMP.
    $caseTemp = Join-Path $CaseDirectory 'tmp'
    New-Item -ItemType Directory -Path $caseTemp -Force | Out-Null
    $env:TMP = $caseTemp
    $env:TEMP = $caseTemp
    # [IO.Path]::GetTempPath() reads TMPDIR on Linux (the CI runner), TEMP/TMP on Windows.
    $env:TMPDIR = $caseTemp
    $global:SimulationCase = $WorkerCase
    $global:CallFile = Join-Path $CaseDirectory 'calls.jsonl'
    $global:SimProjects = @{}
    $global:SimContainers = @{}
    $global:SimProcesses = @{}
    $global:EnableCimFailure = $false
    $global:BadProject = ''
    $global:ChildResourceRegistered = $false
    function global:Record-Call([string]$Name, [object[]]$Arguments) {
        @{ name = $Name; arguments = @($Arguments) } | ConvertTo-Json -Compress -Depth 10 |
            Add-Content -LiteralPath $global:CallFile -Encoding utf8
    }
    # Shadow commands inside the child before loading any application code.
    # A missing simulation branch fails closed instead of reaching the host.
    function global:docker {
        Record-Call 'docker' $args
        $global:LASTEXITCODE = 0
        $command = $args -join ' '
        if ($command -match '\bup\b') {
            $project = $args[[array]::IndexOf($args, '-p') + 1]
            $global:SimProjects[$project] = $true
            if ($global:SimulationCase -eq 'up-failure') { $global:LASTEXITCODE = 11 }
            return
        }
        if ($command -match '\bdown\b') {
            if ($global:SimulationCase -in @('down-failure', 'test-and-down-failure')) { $global:LASTEXITCODE = 7 }
            elseif ('-p' -in $args) { $global:SimProjects[$args[[array]::IndexOf($args, '-p') + 1]] = $false }
            return
        }
        if ($command -match '^compose .*\bps -a -q$') {
            if ($global:SimulationCase -eq 'inspection-failure') { $global:LASTEXITCODE = 66; return }
            $project = $args[[array]::IndexOf($args, '-p') + 1]
            $compose = $args[[array]::IndexOf($args, '-f') + 1]
            if ($global:SimProjects.ContainsKey($project) -and $global:SimProjects[$project]) {
                $container = "$project-container"
                $global:SimContainers[$container] = @{ project = $project; compose = $compose }
                return $container
            }
            if ($global:SimulationCase -eq 'post-inspection-failure') { $global:LASTEXITCODE = 66 }
            return
        }
        if ($command -match '^inspect ') {
            $container = $global:SimContainers[$args[-1]]
            if (-not $container) { throw 'Inspection of unknown synthetic container' }
            $project = if ($global:SimulationCase -eq 'wrong-container-owner' -or $container.project -eq $global:BadProject) { 'foreign-project' } else { $container.project }
            $compose = if ($global:SimulationCase -eq 'wrong-compose-file') { 'C:/foreign/compose.yml' } else { $container.compose }
            return (@{ 'com.docker.compose.project' = $project; 'com.docker.compose.project.config_files' = $compose } | ConvertTo-Json -Compress)
        }
        if ($command -match '\bport\b') { return '127.0.0.1:49991' }
        if ($command -match '^ps -a ') {
            if ($global:SimulationCase -eq 'inspection-failure') { $global:LASTEXITCODE = 66; return }
            return @('dc-contract-test-run-a', 'dc-contract-test-run-b', 'foreign-project')
        }
        if ($command -match '^ps ') {
            if ($global:SimulationCase -eq 'inspection-failure') { $global:LASTEXITCODE = 66 }
            return
        }
        throw "Unsimulated docker command: $command"
    }
    function global:uv {
        Record-Call 'uv' $args
        Register-MockedChildResource
        $global:LASTEXITCODE = if ($global:SimulationCase -eq 'test-and-down-failure') { 19 } else { 0 }
    }
    function global:Register-MockedChildResource {
        if ($global:SimulationCase -notlike 'wrapper-*-cleanup-failure' -or $global:ChildResourceRegistered) { return }
        $number = 900102
        $command = "node $RepoRoot/frontend/synthetic-child.mjs"
        $global:SimProcesses[$number] = [pscustomobject]@{ ProcessId = $number; ParentProcessId = $PID; CreationDate = [datetime]'2026-10-05T00:00:00Z'; CommandLine = $command }
        Register-TestProcess -RunManifest $env:DC_TEST_RUN_MANIFEST -ProcessId $number -CommandLine $command -ScopeId $env:DC_TEST_SCOPE_ID
        $global:ChildResourceRegistered = $true
    }
    function global:npm { Record-Call 'npm' $args; Register-MockedChildResource; $global:LASTEXITCODE = 0 }
    function global:npx { Record-Call 'npx' $args; Register-MockedChildResource; $global:LASTEXITCODE = 0 }
    function global:node { Record-Call 'node' $args; Register-MockedChildResource; $global:LASTEXITCODE = 0 }
    function global:pwsh { Record-Call 'child-runner' $args; Register-MockedChildResource; $global:LASTEXITCODE = 0 }
    function global:Get-NetTCPConnection {
        param($LocalPort, $State, $ErrorAction)
        Record-Call 'network' @($LocalPort)
        if ($global:SimulationCase -eq 'global-cleanup') {
            return @([pscustomobject]@{ LocalAddress = '127.0.0.1'; OwningProcess = 900101 })
        }
        if ($global:SimulationCase -eq 'occupied-port') {
            return [pscustomobject]@{ LocalAddress = '127.0.0.1'; LocalPort = 18001; OwningProcess = 900999 }
        }
        if ($global:SimulationCase -eq 'web-ports' -or $global:SimulationCase -like 'wrapper-*') { return }
        throw 'Simulated network inspection unavailable'
    }
    function global:Get-Process {
        param($Id, $ErrorAction)
        Record-Call 'process-inspect' @($Id)
        return [pscustomobject]@{ Id = $Id; ProcessName = 'node' }
    }
    function global:Get-CimInstance {
        param($ClassName, $Filter, $ErrorAction)
        Record-Call 'cim' @($ClassName, $Filter)
        if ($global:EnableCimFailure) { throw 'Simulated CIM unavailable' }
        if ($Filter -notmatch '^ProcessId=([0-9]+)$') { throw "Unbounded process discovery: $Filter" }
        return $global:SimProcesses[[int]$Matches[1]]
    }
    function global:Stop-Process {
        param($Id, [switch]$Force, $ErrorAction)
        Record-Call 'process-stop' @($Id)
        if ($global:SimulationCase -eq 'process-stop-failure' -or $global:SimulationCase -like 'wrapper-*-cleanup-failure') { throw 'Simulated process stop unavailable' }
        if ($global:SimulationCase -ne 'process-survives') { $global:SimProcesses.Remove([int]$Id) }
    }
    function global:Start-Process {
        param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
        if ($global:SimulationCase -notlike 'web-script-*') { throw 'Starting a service is forbidden in this test' }
        Record-Call 'process-start' @($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
        if ($WindowStyle -ne 'Hidden') { throw 'Visible service launch forbidden' }
        $global:SimProcesses[900102] = [pscustomobject]@{ ProcessId = 900102; ParentProcessId = $PID; CreationDate = [datetime]'2026-10-05T00:00:00Z'; CommandLine = "node $($ArgumentList -join ' ')" }
        if ($global:SimulationCase -eq 'web-script-inspection-failure') { $global:EnableCimFailure = $true }
        $process = [pscustomobject]@{ Id = 900102; HasExited = $false; ExitCode = 19 }
        $process | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { $this.HasExited = $true; $global:SimProcesses.Remove(900102) }
        return $process
    }
    function global:Wait-Process { throw 'Waiting on a host process is forbidden in this test' }
    function global:Get-Random { param($Minimum, $Maximum) return ([int]$Minimum + 3001) }
    if (-not $Baseline) {
        . $HelperScript
        function New-SyntheticRun([string]$Label) {
            $generated = Initialize-TestRun -RepoRoot $RepoRoot
            $manifest = Join-Path $CaseDirectory "$Label.json"
            Copy-Item -LiteralPath $generated -Destination $manifest
            return $manifest
        }
        function Add-SyntheticProject([string]$Manifest, [string]$Suffix) {
            $state = Get-TestRun $Manifest
            $project = "dc-verify-$($state.run_id)-$Suffix"
            Register-TestProject -RunManifest $Manifest -ProjectName $project -ComposeFile (Join-Path $RepoRoot 'api/docker-compose.contract-tests.yml')
            $global:SimProjects[$project] = $true
            return $project
        }
        function Add-SyntheticProcess([string]$Manifest, [int]$Number, [string]$Scope = '') {
            $command = "node $RepoRoot/frontend/synthetic-server.mjs"
            $global:SimProcesses[$Number] = [pscustomobject]@{ ProcessId = $Number; ParentProcessId = $PID; CreationDate = [datetime]'2026-10-05T00:00:00Z'; CommandLine = $command }
            Register-TestProcess -RunManifest $Manifest -ProcessId $Number -CommandLine $command -ScopeId $Scope
        }
        if ($WorkerCase -eq 'helper-prune') {
            try {
                $registries = Join-Path $caseTemp 'dc-test-runs'
                function New-AgedRegistry([string]$Status, [int]$AgeDays, [switch]$Owning) {
                    $id = [guid]::NewGuid().ToString('N')
                    $directory = Join-Path $registries $id
                    New-Item -ItemType Directory -Path $directory -Force | Out-Null
                    $projects = @()
                    if ($Owning) { $projects = @([pscustomobject]@{name="dc-verify-$id-aaaaaaaaaa";status='active'}) }
                    $manifest = Join-Path $directory 'run.json'
                    [ordered]@{version=1;run_id=$id;repo_root=$RepoRoot;status=$Status;projects=$projects;processes=@();web=$null} |
                        ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifest -Encoding utf8
                    (Get-Item -LiteralPath $manifest).LastWriteTimeUtc = [DateTime]::UtcNow.AddDays(-$AgeDays)
                    return $id
                }
                $removed = @((New-AgedRegistry active 2), (New-AgedRegistry stopped 8))
                $kept = @((New-AgedRegistry active 0), (New-AgedRegistry stopped 2), (New-AgedRegistry active 30 -Owning))
                $null = Initialize-TestRun -RepoRoot $RepoRoot
                foreach ($id in $removed) { if (Test-Path -LiteralPath (Join-Path $registries $id)) { throw "Stale registry kept: $id" } }
                foreach ($id in $kept) { if (-not (Test-Path -LiteralPath (Join-Path $registries $id))) { throw "Live or recent registry removed: $id" } }
            }
            catch { Write-Host "SIMULATED_FAILURE $($_.Exception.Message)"; exit 1 }
            exit 0
        }
        $manifest = New-SyntheticRun 'run-b'
        if ($WorkerCase -like 'helper-*' -or $WorkerCase -in @('pid-reused','command-changed','process-inspection-failure','process-stop-failure','process-survives','wrong-registry','wrong-checkout','wrong-container-owner','wrong-compose-file','unregistered-project','unregistered-process','process-wrong-parent','process-wrong-command','occupied-port','web-ports','network-inspection-failure')) {
            try {
                switch ($WorkerCase) {
                    'helper-two-runs' {
                        $other = New-SyntheticRun 'run-a'
                        $projectA = Add-SyntheticProject $other 'aaaaaaaaaa'
                        $projectB = Add-SyntheticProject $manifest 'bbbbbbbbbb'
                        Add-SyntheticProcess $other 900101
                        Add-SyntheticProcess $manifest 900102
                        Stop-TestRun -RunManifest $manifest
                        if (-not $global:SimProjects[$projectA] -or -not $global:SimProcesses.ContainsKey(900101)) { throw 'Run A was changed by cleanup of B' }
                        if ($global:SimProjects[$projectB] -or $global:SimProcesses.ContainsKey(900102)) { throw 'Run B was not cleaned' }
                        if ((Get-TestRun $other).status -ne 'active' -or (Get-TestRun $manifest).status -ne 'stopped') { throw 'Wrong registry status after cleanup' }
                    }
                    'helper-two-scopes' {
                        $project = Add-SyntheticProject $manifest 'bbbbbbbbbb'
                        Add-SyntheticProcess $manifest 900101 'scope-a'
                        Add-SyntheticProcess $manifest 900102 'scope-b'
                        Stop-TestRun -RunManifest $manifest -ScopeId 'scope-b'
                        if (-not $global:SimProjects[$project] -or -not $global:SimProcesses.ContainsKey(900101) -or $global:SimProcesses.ContainsKey(900102)) { throw 'Scoped cleanup touched another resource' }
                        if ((Get-TestRun $manifest).status -ne 'active') { throw 'Scoped cleanup completed the whole invocation' }
                    }
                    'helper-idempotent' {
                        $null = Add-SyntheticProject $manifest 'bbbbbbbbbb'
                        Stop-TestRun -RunManifest $manifest
                        Stop-TestRun -RunManifest $manifest
                    }
                    'helper-partial-cleanup' {
                        $bad = Add-SyntheticProject $manifest 'aaaaaaaaaa'
                        $good = Add-SyntheticProject $manifest 'bbbbbbbbbb'
                        $global:BadProject = $bad
                        $failed = $false
                        try { Stop-TestRun -RunManifest $manifest } catch { $failed = $true }
                        $state = Get-TestRun $manifest
                        if (-not $failed -or -not $global:SimProjects[$bad] -or $global:SimProjects[$good] -or $state.status -ne 'active') { throw 'Partial cleanup was not preserved honestly' }
                        if (($state.projects | Where-Object name -eq $bad).status -ne 'active' -or ($state.projects | Where-Object name -eq $good).status -ne 'stopped') { throw 'Registry lost cleanup outcomes' }
                    }
                    { $_ -in @('pid-reused','command-changed','process-inspection-failure','process-stop-failure','process-survives') } {
                        Add-SyntheticProcess $manifest 900102
                        if ($WorkerCase -eq 'process-inspection-failure') { $global:EnableCimFailure = $true }
                        if ($WorkerCase -eq 'pid-reused') { $global:SimProcesses[900102].CreationDate = [datetime]'2026-10-05T01:00:00Z' }
                        if ($WorkerCase -eq 'command-changed') { $global:SimProcesses[900102].CommandLine = 'node C:/foreign/server.mjs' }
                        Stop-TestRun -RunManifest $manifest
                    }
                    'wrong-registry' {
                        $state = Get-TestRun $manifest
                        $state.run_id = 'not-an-owner'
                        $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifest
                        Stop-TestRun -RunManifest $manifest
                    }
                    'wrong-checkout' {
                        $state = Get-TestRun $manifest
                        $state.repo_root = 'C:/foreign/checkout'
                        $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifest
                        Stop-TestRun -RunManifest $manifest
                    }
                    { $_ -in @('wrong-container-owner','wrong-compose-file') } {
                        $null = Add-SyntheticProject $manifest 'bbbbbbbbbb'
                        Stop-TestRun -RunManifest $manifest
                    }
                    'unregistered-project' { Stop-TestRun -RunManifest $manifest -ProjectName 'foreign-project' }
                    'unregistered-process' { Stop-TestRun -RunManifest $manifest -ProcessId 900999 }
                    { $_ -in @('process-wrong-parent','process-wrong-command') } {
                        $command = "node $RepoRoot/frontend/synthetic-server.mjs"
                        $global:SimProcesses[900102] = [pscustomobject]@{ ProcessId = 900102; ParentProcessId = ($PID + 1); CreationDate = [datetime]'2026-10-05T00:00:00Z'; CommandLine = $command }
                        if ($WorkerCase -eq 'process-wrong-command') { $global:SimProcesses[900102].ParentProcessId = $PID; $command = 'different caller command' }
                        Register-TestProcess -RunManifest $manifest -ProcessId 900102 -CommandLine $command
                    }
                    { $_ -in @('occupied-port','web-ports','network-inspection-failure') } {
                        $web = Set-TestRunWeb -RunManifest $manifest
                        if ($web.app_port -in @(5173,4174) -or $web.source_port -in @(5173,4174)) { throw 'Legacy web port reused' }
                    }
                    default { throw "Unknown helper scenario: $WorkerCase" }
                }
            }
            catch { Write-Host "SIMULATED_FAILURE $($_.Exception.Message)"; exit 1 }
            exit 0
        }
        if ($WorkerCase -like 'web-script-*') {
            $global:SimulationCase = 'web-ports'
            $null = Set-TestRunWeb -RunManifest $manifest
            $global:SimulationCase = $WorkerCase
        }
        $names = @('DATABASE_URL','CONTRACT_TEST_DATABASE_URL','ALLOW_DESTRUCTIVE_DB_TESTS','DEBUG','DC_TEST_RUN_MANIFEST','DC_TEST_RUN_ID','DC_TEST_PROJECT','DPE_SOURCE_PORT')
        $expected = @{}
        foreach ($name in $names) {
            $expected[$name] = "synthetic-before-$name"
            [Environment]::SetEnvironmentVariable($name, $expected[$name])
        }
        $registryLock = $null
        if ($WorkerCase -in @('registration-failure-env','web-script-registration-failure')) { $registryLock = [IO.File]::Open("$manifest.lock", 'OpenOrCreate', 'ReadWrite', 'None') }
        $resultCode = 1
        try {
            if ($WorkerCase -like 'wrapper-*') {
                if ($WorkerCase -eq 'wrapper-all-init-failure') {
                    $state = Get-TestRun $manifest
                    $state.repo_root = 'C:/foreign/checkout'
                    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifest
                }
                if ($WorkerCase -like '*-standalone') { Remove-Item Env:DC_TEST_RUN_MANIFEST -ErrorAction SilentlyContinue }
                if ($WorkerCase -like 'wrapper-backend-*') {
                    if ($WorkerCase -like '*-standalone') { & $TargetScript -SkipLint -Paths 'tests/synthetic-only.py' }
                    else { & $TargetScript -RunManifest $manifest -SkipLint -Paths 'tests/synthetic-only.py' }
                }
                elseif ($WorkerCase -like 'wrapper-frontend-*') {
                    if ($WorkerCase -like '*-standalone') { & $TargetScript -Gate unit }
                    else { & $TargetScript -RunManifest $manifest -Gate unit }
                }
                elseif ($WorkerCase -like 'wrapper-postgis-*') { & $TargetScript -RunManifest $manifest -Paths 'tests/integration/synthetic-only.py' }
                else { & $TargetScript -RunManifest $manifest }
            }
            elseif ($WorkerCase -like 'web-script-*') {
                & $TargetScript -RunManifest $manifest -Kind source
            }
            elseif ($WorkerCase -in @('global-cleanup', 'inspection-failure')) {
                $null = Add-SyntheticProject $manifest 'bbbbbbbbbb'
                & $TargetScript -RunManifest $manifest
            }
            else { & $TargetScript -RunManifest $manifest 'tests/integration/synthetic-only.py' }
            $resultCode = $LASTEXITCODE
        }
        catch { Write-Host "SIMULATED_FAILURE $($_.Exception.Message)" }
        finally {
            if ($registryLock) { $registryLock.Dispose() }
            $environmentRestored = $true
            foreach ($name in $names) {
                if ([Environment]::GetEnvironmentVariable($name) -ne $expected[$name]) { $environmentRestored = $false }
            }
            @{ restored = $environmentRestored } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $CaseDirectory 'environment.json')
        }
        exit $resultCode
    }
    if ($WorkerCase -in @('global-cleanup', 'inspection-failure')) {
        & $TargetScript
    }
    else {
        & $TargetScript 'tests/integration/synthetic-only.py'
    }
    exit $LASTEXITCODE
}

$reportRoot = Join-Path $RepoRoot 'reports/reliability-lot0'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$shell = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
$sourceRoot = if ($Baseline) { Join-Path $reportRoot 'baseline' } else { $RepoRoot }
$cases = @(
    @{ name = 'global-cleanup'; script = 'scripts/stop-resources.ps1'; expectation = 'no-foreign-resource' },
    @{ name = 'inspection-failure'; script = 'scripts/stop-resources.ps1'; expectation = 'inspection-nonzero' },
    @{ name = 'up-failure'; script = 'api/backend/scripts/run_contract_integration.ps1'; expectation = 'partial-up-cleaned' },
    @{ name = 'down-failure'; script = 'api/backend/scripts/run_contract_integration.ps1'; expectation = 'cleanup-nonzero' },
    @{ name = 'test-and-down-failure'; script = 'api/backend/scripts/run_contract_integration.ps1'; expectation = 'test-exit-preserved' }
)
if (-not $Baseline) {
    $cases += @(
        @{ name = 'helper-two-runs'; expectation = 'helper-success' },
        @{ name = 'helper-two-scopes'; expectation = 'helper-success' },
        @{ name = 'helper-idempotent'; expectation = 'idempotent-success' },
        @{ name = 'helper-partial-cleanup'; expectation = 'helper-success' },
        @{ name = 'helper-prune'; expectation = 'helper-success' },
        @{ name = 'pid-reused'; expectation = 'refuse-without-stop' },
        @{ name = 'command-changed'; expectation = 'refuse-without-stop' },
        @{ name = 'wrong-registry'; expectation = 'refuse-without-stop' },
        @{ name = 'wrong-checkout'; expectation = 'refuse-without-stop' },
        @{ name = 'unregistered-project'; expectation = 'refuse-without-stop' },
        @{ name = 'unregistered-process'; expectation = 'refuse-without-stop' },
        @{ name = 'process-wrong-parent'; expectation = 'refuse-without-stop' },
        @{ name = 'process-wrong-command'; expectation = 'refuse-without-stop' },
        @{ name = 'wrong-container-owner'; expectation = 'refuse-without-stop' },
        @{ name = 'wrong-compose-file'; expectation = 'refuse-without-stop' },
        @{ name = 'process-inspection-failure'; expectation = 'refuse-without-stop' },
        @{ name = 'process-stop-failure'; expectation = 'process-stop-nonzero' },
        @{ name = 'process-survives'; expectation = 'process-stop-nonzero' },
        @{ name = 'post-inspection-failure'; script = 'api/backend/scripts/run_contract_integration.ps1'; expectation = 'cleanup-nonzero' },
        @{ name = 'occupied-port'; expectation = 'refuse-without-stop' },
        @{ name = 'network-inspection-failure'; expectation = 'refuse-without-stop' },
        @{ name = 'web-ports'; expectation = 'helper-success' },
        @{ name = 'harness-env'; script = 'api/backend/scripts/run_contract_integration.ps1'; expectation = 'environment-success' },
        @{ name = 'registration-failure-env'; script = 'api/backend/scripts/run_contract_integration.ps1'; expectation = 'environment-failure' },
        @{ name = 'web-script-registration-failure'; script = 'scripts/start-test-web.ps1'; expectation = 'web-registration-cleaned' },
        @{ name = 'web-script-inspection-failure'; script = 'scripts/start-test-web.ps1'; expectation = 'web-inspection-unverified' },
        @{ name = 'web-script-test-failure'; script = 'scripts/start-test-web.ps1'; expectation = 'web-test-exit-preserved' },
        @{ name = 'wrapper-all-cleanup-failure'; script = 'scripts/test-all.ps1'; expectation = 'matrix-cleanup-failure' },
        @{ name = 'wrapper-all-init-failure'; script = 'scripts/test-all.ps1'; expectation = 'matrix-init-failure' },
        @{ name = 'wrapper-backend-cleanup-failure'; script = 'scripts/test-backend.ps1'; expectation = 'wrapper-cleanup-failure' },
        @{ name = 'wrapper-frontend-cleanup-failure'; script = 'scripts/test-frontend.ps1'; expectation = 'wrapper-cleanup-failure' },
        @{ name = 'wrapper-postgis-cleanup-failure'; script = 'scripts/test-postgis.ps1'; expectation = 'wrapper-cleanup-failure' },
        @{ name = 'wrapper-e2e-cleanup-failure'; script = 'scripts/test-e2e.ps1'; expectation = 'wrapper-cleanup-failure' },
        @{ name = 'wrapper-backend-standalone'; script = 'scripts/test-backend.ps1'; expectation = 'standalone-no-service' },
        @{ name = 'wrapper-frontend-standalone'; script = 'scripts/test-frontend.ps1'; expectation = 'standalone-no-service' }
    )
}
$results = @()
foreach ($case in $cases) {
    $directory = Join-Path $reportRoot ($(if ($Baseline) { 'baseline-' } else { 'current-' }) + $case.name)
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $callFile = Join-Path $directory 'calls.jsonl'
    if (Test-Path -LiteralPath $callFile) { Remove-Item -LiteralPath $callFile }
    $target = if ($case.ContainsKey('script')) { Join-Path $sourceRoot $case.script } else { '' }
    $arguments = @('-NoProfile','-File',$PSCommandPath,'-WorkerCase',$case.name,'-TargetScript',$target,'-CaseDirectory',$directory,'-HelperScript',(Join-Path $RepoRoot 'scripts/test-resources.ps1'))
    if ($Baseline) { $arguments += '-Baseline' }
    & $shell @arguments *> (Join-Path $directory 'output.log')
    $code = $LASTEXITCODE
    $calls = @(if (Test-Path -LiteralPath $callFile) { Get-Content -LiteralPath $callFile | ForEach-Object { $_ | ConvertFrom-Json } })
    $downs = @($calls | Where-Object { $_.name -eq 'docker' -and 'down' -in $_.arguments })
    $pass = switch ($case.expectation) {
        'no-foreign-resource' { @($downs | Where-Object { 'dc-contract-test-run-a' -in $_.arguments -or 'dc-contract-test-run-b' -in $_.arguments -or 'foreign-project' -in $_.arguments }).Count -eq 0 -and @($calls | Where-Object name -eq 'process-stop').Count -eq 0 }
        'inspection-nonzero' { $code -ne 0 }
        'partial-up-cleaned' { $downs.Count -eq 1 -and $code -ne 0 }
        'cleanup-nonzero' { $downs.Count -eq 1 -and $code -ne 0 }
        'test-exit-preserved' { $code -eq 19 -and $downs.Count -eq 1 }
        'helper-success' { $code -eq 0 }
        'idempotent-success' { $code -eq 0 -and $downs.Count -eq 1 }
        'refuse-without-stop' { $code -ne 0 -and $downs.Count -eq 0 -and @($calls | Where-Object name -eq 'process-stop').Count -eq 0 }
        'failure-nonzero' { $code -ne 0 }
        'process-stop-nonzero' { $code -ne 0 -and @($calls | Where-Object name -eq 'process-stop').Count -eq 1 }
        'environment-success' { $code -eq 0 -and (Get-Content -LiteralPath (Join-Path $directory 'environment.json') -Raw | ConvertFrom-Json).restored }
        'environment-failure' { $code -ne 0 -and (Get-Content -LiteralPath (Join-Path $directory 'environment.json') -Raw | ConvertFrom-Json).restored -and $downs.Count -eq 0 }
        'web-registration-cleaned' { $code -ne 0 -and @($calls | Where-Object name -eq 'process-start').Count -eq 1 -and @($calls | Where-Object name -eq 'process-stop').Count -eq 1 }
        'web-inspection-unverified' { $code -ne 0 -and @($calls | Where-Object name -eq 'process-start').Count -eq 1 -and @($calls | Where-Object name -eq 'process-stop').Count -eq 0 -and (Get-Content -LiteralPath (Join-Path $directory 'output.log') -Raw) -match 'cleanup is unverified' }
        'web-test-exit-preserved' { $code -eq 19 -and @($calls | Where-Object name -eq 'process-start').Count -eq 1 }
        'matrix-cleanup-failure' { $code -ne 0 -and @($calls | Where-Object name -eq 'process-stop').Count -ge 1 -and (Get-Content -LiteralPath (Join-Path $directory 'output.log') -Raw) -match 'RESULT backend=PASS postgis=PASS frontend=PASS e2e=PASS cleanup=FAIL' }
        'matrix-init-failure' { $code -ne 0 -and $calls.Count -eq 0 -and (Get-Content -LiteralPath (Join-Path $directory 'output.log') -Raw) -match 'RESULT backend=SKIP postgis=SKIP frontend=SKIP e2e=SKIP cleanup=PASS' }
        'wrapper-cleanup-failure' { $code -ne 0 -and @($calls | Where-Object name -eq 'process-stop').Count -ge 1 -and (Get-Content -LiteralPath (Join-Path $directory 'output.log') -Raw) -notmatch '(?m)^(BACKEND|FRONTEND|POSTGIS|E2E) ok\s*$' }
        'standalone-no-service' { $code -eq 0 -and @($calls | Where-Object { $_.name -in @('docker','cim','network','process-stop','process-start') }).Count -eq 0 }
    }
    $results += [pscustomobject]@{ case = $case.name; passed = [bool]$pass; exit_code = $code; cleanup_calls = $downs.Count }
    Write-Host "CASE $($case.name) passed=$pass exit=$code cleanup=$($downs.Count)"
}
$results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $reportRoot ($(if ($Baseline) { 'baseline-results.json' } else { 'results.json' }))) -Encoding utf8
if (@($results | Where-Object { -not $_.passed }).Count) { exit 1 }
exit 0

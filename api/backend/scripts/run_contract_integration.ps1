<#
.SYNOPSIS
    Provision a disposable PostGIS container, run the given pytest paths
    against it, then tear the owned container down on normal success or failure.

.DESCRIPTION
    Wave 0 harness for Phase 43 (contrats semantiques & golden cases).
    Each invocation gets its own docker compose project name and an
    ephemeral host port (no fixed global port, no persistent volume — see
    docker-compose.contract-tests.yml) so parallel runs never collide and
    a known manifest supports manual recovery after a terminated owner. `docker compose down -v --remove-orphans`
    runs in a `finally` block, including after a pytest failure or an
    unexpected script error.

    T-43-01 mitigation: the DSN used to run the tests is built ENTIRELY by
    this script from a container it just started — it never accepts an
    inbound DATABASE_URL override — and the resolved host is checked against
    an explicit loopback allow-list before any test runs.

.PARAMETER TestPaths
    One or more pytest paths/expressions, forwarded verbatim to `pytest`.

.EXAMPLE
    pwsh -NoProfile -File scripts/run_contract_integration.ps1 tests/integration/test_postgis_harness.py
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [Parameter(Position = 0, Mandatory = $true, ValueFromRemainingArguments = $true)]
    [string[]]$TestPaths
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# -- Paths --------------------------------------------------------------------
$ScriptDir = $PSScriptRoot
$BackendDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$ApiDir = (Resolve-Path (Join-Path $BackendDir "..")).Path
$ComposeFile = Join-Path $ApiDir "docker-compose.contract-tests.yml"

if (-not (Test-Path $ComposeFile)) {
    throw "Compose file introuvable: $ComposeFile"
}

# Own one project, even when nested under a parent invocation.
$RepoRoot = (Resolve-Path (Join-Path $ApiDir '..')).Path
. (Join-Path $RepoRoot 'scripts/test-resources.ps1')
$OwnTestRun = -not $RunManifest
$SavedEnvironment = Save-TestEnvironment @('DATABASE_URL','CONTRACT_TEST_DATABASE_URL',
    'ALLOW_DESTRUCTIVE_DB_TESTS','DEBUG','DC_TEST_RUN_MANIFEST','DC_TEST_RUN_ID','DC_TEST_PROJECT')
$RunInitialized = $false
$ProjectRegistered = $false
$ProjectName = ''

$PgDb = "contract_test"
$PgUser = "contract_test"
$PgPassword = "contract_test"

# T-43-01: allow-list of hosts this harness is permitted to target. The DSN
# is always built below from the port docker compose just published for the
# container WE started — this check is defense-in-depth, not the primary
# control, but it guarantees the script refuses to run tests against
# anything that doesn't look like a local loopback container.
$AllowedHosts = @("127.0.0.1", "localhost")

$ExitCode = 1
$CleanupFailed = $false

try {
    $RunManifest = Initialize-TestRun -RepoRoot $RepoRoot -RunManifest $RunManifest
    $RunInitialized = $true
    Set-TestRunEnvironment $RunManifest
    $ProjectName = "dc-verify-$($env:DC_TEST_RUN_ID)-$([guid]::NewGuid().ToString('N').Substring(0, 10))"
    $env:DC_TEST_PROJECT = $ProjectName
    Register-TestProject -RunManifest $RunManifest -ProjectName $ProjectName -ComposeFile $ComposeFile

    $ProjectRegistered = $true
    Write-Host "==> Provisioning PostGIS jetable (project: $ProjectName)"
    & docker compose -f $ComposeFile -p $ProjectName up -d --wait --wait-timeout 60
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up a echoue (exit $LASTEXITCODE) — voir logs ci-dessus"
    }

    # -- Discover the ephemeral host port published for `db` ------------------
    $PortMapping = (& docker compose -f $ComposeFile -p $ProjectName port db 5432 | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $PortMapping) {
        throw "Impossible de resoudre le port publie par le conteneur db"
    }

    $PortMapping = $PortMapping.Trim()
    $Parts = $PortMapping.Split(":")
    if ($Parts.Length -ne 2) {
        throw "Format de port inattendu: '$PortMapping'"
    }
    $HostPart = $Parts[0]
    $PortPart = $Parts[1]

    if ($AllowedHosts -notcontains $HostPart) {
        throw "T-43-01: hostname resolu '$HostPart' hors allow-list ($($AllowedHosts -join ', ')) — refus de cibler cette base"
    }

    $Dsn = "postgresql+asyncpg://${PgUser}:${PgPassword}@${HostPart}:${PortPart}/${PgDb}"
    Write-Host "==> DSN de test: postgresql+asyncpg://${PgUser}:***@${HostPart}:${PortPart}/${PgDb}"

    # -- Run the requested pytest paths against the disposable DB -------------
    # DATABASE_URL/CONTRACT_TEST_DATABASE_URL are built from the disposable
    # container above. The destructive-test opt-in is scoped to this harness,
    # which is the only supported entry point for tests/integration/.
    $env:DATABASE_URL = $Dsn
    $env:CONTRACT_TEST_DATABASE_URL = $Dsn
    $env:ALLOW_DESTRUCTIVE_DB_TESTS = "1"
    $env:DEBUG = "true"

    Push-Location $BackendDir
    try {
        & uv run python -m pytest @TestPaths -v -m destructive -p scripts.contract_test_gate
        $ExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
catch {
    Write-Error $_ -ErrorAction Continue
    $ExitCode = 1
}
finally {
    try {
        Write-Host "==> Nettoyage PostGIS jetable (project: $ProjectName)"
        if ($ProjectRegistered) { Stop-TestRun -RunManifest $RunManifest -ProjectName $ProjectName }
        if ($OwnTestRun -and $RunInitialized) { Stop-TestRun -RunManifest $RunManifest }
    }
    catch {
        $CleanupFailed = $true
        Write-Error $_ -ErrorAction Continue
    }
    finally {
        Restore-TestEnvironment $SavedEnvironment
    }
}

if ($CleanupFailed -and $ExitCode -eq 0) { $ExitCode = 1 }
exit $ExitCode

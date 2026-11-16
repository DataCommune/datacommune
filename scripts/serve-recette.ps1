<#
.SYNOPSIS
    Sert la recette de Ianis sur le reseau local, le temps de la seance (plan 03-13, D-36, D-53).

.DESCRIPTION
    Lance `vite dev` lie a UNE adresse IPv4 privee du poste (jamais toutes les
    interfaces), port alloue a cette execution, pour que le telephone et le PC portable ouvrent
    l'application. Le navigateur passe par le relais de vite et le rendu
    serveur par `handleFetch` (hooks.server.ts), tous deux vers l'API de la
    base de travail (DEV_API_PROXY_TARGET).

    Prerequis (README, Procedure locale) :
      - SHA de la phase deploye ; fenetre de l'API ouverte par
        `workbase-recette.sh up` (script prive de l'hyperviseur) ;
      - cle publique de la seance dans $env:PUBLIC_DC_API_KEY (tiree sur le
        poste, enregistree par `workbase-anon-key.sh`, script prive) ; sa valeur
        n'est jamais imprimee.

    Le lanceur attend le serveur cache jusqu'a Ctrl+C ; le nettoyage de cette invocation
    est effectue en `finally` et libere le port alloue a cette execution, y compris lie au reseau
    local. Fin de seance : `workbase-recette.sh down`.

    Arret sans ecouteur restant (C43, WINDOWS n° 84 et 98) : tout ce que lance le script vit dans
    un objet job Windows ferme avec lui (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) ; un processus tue,
    une fenetre fermee ou un Ctrl+C emportent l'arbre entier (cale de Volta, cmd, node, esbuild).
    Le script surveille aussi le processus qui l'a lance : a la fin de ce parent (arret d'une
    tache par l'orchestrateur, TaskStop), il s'arrete et nettoie. En fin d'invocation, il arrete
    ce qui reste dans son job puis verifie qu'aucun ecouteur ne reste sur <ip>:<port> :
    `RECETTE stop listeners=0` (sortie non nulle sinon).

    Choix de l'interface (WR-04) : un client VPN full-tunnel porte la route
    par defaut avec une adresse privee ; sans refus, le bundle (cle de la
    seance) et le relais vers l'API de la base de travail s'exposeraient sur
    le reseau du VPN. Le script ne retient donc qu'une interface LAN sans
    ambiguite, refuse toute interface tunnel, PPP ou virtuelle proprietaire
    (types IANA 131, 23, 53) et imprime celle qu'il retient :
    `RECETTE host <ip> interface=<alias> type=<type> description=<description>`.

.PARAMETER LanHost
    Adresse IPv4 privee du poste a laquelle lier le serveur. Par defaut :
    l'unique interface LAN candidate (Up, passerelle IPv4, adresse privee,
    type ni tunnel ni PPP ni virtuel proprietaire) ; refus si aucune ou
    plusieurs. Une adresse explicite portee par une interface tunnel est
    refusee.

.PARAMETER ApiTarget
    Origine de l'API (http://<ipv4>:8000). Par defaut : WORKBASE_IP du
    reglage local de l'hyperviseur (ignore par git), port 8000.

.PARAMETER ResolveOnly
    Imprime la ligne `RECETTE host ...` de l'interface retenue et sort 0,
    sans exiger la cle ni rien servir (verification avant la seance).

.EXAMPLE
    pwsh -NoProfile -File scripts/serve-recette.ps1

.EXAMPLE
    pwsh -NoProfile -File scripts/serve-recette.ps1 -ResolveOnly
#>

[CmdletBinding()]
param(
    [string]$RunManifest = $env:DC_TEST_RUN_MANIFEST,
    [string]$LanHost,
    [string]$ApiTarget,
    [switch]$ResolveOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'test-resources.ps1')
$InvocationRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OwnTestRun = -not $RunManifest
$SavedRunEnvironment = Save-TestEnvironment @('DC_TEST_RUN_MANIFEST','DC_TEST_RUN_ID',
    'DC_TEST_SCOPE_ID','E2E_APP_ORIGIN','E2E_SOURCE_ORIGIN','VITE_API_URL',
    'PUBLIC_DC_API_KEY','DEV_API_PROXY_TARGET')
$RunInitialized = $false
$ResourceScope = ''
$Port = $null
$LanBound = $null
$Job = [IntPtr]::Zero
$global:LASTEXITCODE = 0
$RunnerExit = 1

# C43: stops every process of the job of this invocation but this one.
function Stop-RecetteJobProcesses([IntPtr]$JobHandle) {
    foreach ($id in [DcRecetteJob]::ProcessIds($JobHandle)) {
        if ($id -ne $PID) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    }
}

# C43: number of listeners left on <Address>:<Port> after waiting up to <Seconds>.
function Wait-NoListener([string]$Address, [int]$Port, [int]$Seconds) {
    $deadline = [datetime]::UtcNow.AddSeconds($Seconds)
    do {
        $count = @(Get-NetTCPConnection -State Listen -LocalAddress $Address -LocalPort $Port -ErrorAction SilentlyContinue).Count
        if (-not $count) { return 0 }
        Start-Sleep -Milliseconds 250
    } while ([datetime]::UtcNow -lt $deadline)
    return $count
}

try {
    $RunManifest = Initialize-TestRun -RepoRoot $InvocationRoot -RunManifest $RunManifest
    $RunInitialized = $true
    Set-TestRunEnvironment $RunManifest
    $ResourceScope = [guid]::NewGuid().ToString('N')
    $env:DC_TEST_SCOPE_ID = $ResourceScope


    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
    $FrontendDir = Join-Path $RepoRoot 'frontend'
    # No host-wide cleanup script is called by this runner.

    function Test-PrivateIPv4([string]$Address) {
        $ip = $null
        if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$ip)) { return $false }
        if ($ip.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }
        $b = $ip.GetAddressBytes()
        return ($b[0] -eq 10) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168)
    }

    # IANA interface types never used for the acceptance session: PPP (23), proprietary virtual
    # (53: Wintun/WireGuard, some TAP drivers), tunnel (131).
    $TunnelTypes = @(23, 53, 131)

    function Resolve-LanHost {
        $candidates = @(
            Get-NetIPConfiguration | Where-Object {
                $_.IPv4DefaultGateway -and $_.NetAdapter -and $_.NetAdapter.Status -eq 'Up' -and
                ($TunnelTypes -notcontains [int]$_.NetAdapter.InterfaceType)
            } | ForEach-Object {
                $address = @($_.IPv4Address | ForEach-Object { $_.IPAddress } |
                    Where-Object { Test-PrivateIPv4 $_ }) | Select-Object -First 1
                if ($address) {
                    [pscustomobject]@{
                        Address     = $address
                        Alias       = $_.InterfaceAlias
                        Type        = [int]$_.NetAdapter.InterfaceType
                        Description = $_.InterfaceDescription
                    }
                }
            }
        )
        if ($candidates.Count -eq 0) {
            throw 'Aucune interface LAN candidate : passer -LanHost <ipv4>'
        }
        if ($candidates.Count -gt 1) {
            $list = ($candidates | ForEach-Object { "$($_.Alias)=$($_.Address) ($($_.Description))" }) -join ', '
            throw "Plusieurs interfaces LAN candidates ($list) : passer -LanHost <ipv4>"
        }
        return $candidates[0]
    }

    function Get-LanInterface([string]$Address) {
        # Une seule adresse privee du poste : jamais toutes les interfaces, jamais une adresse publique.
        if (-not (Test-PrivateIPv4 $Address)) {
            throw "LanHost doit etre une adresse IPv4 privee du poste : $Address"
        }
        $own = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -eq $Address })
        if ($own.Count -eq 0) {
            throw "LanHost n'est pas une adresse de ce poste : $Address"
        }
        $adapter = Get-NetAdapter -InterfaceIndex $own[0].InterfaceIndex -ErrorAction SilentlyContinue
        if (-not $adapter) {
            throw "Type d'interface inconnu pour LanHost $Address : la recette se sert sur le reseau local seulement"
        }
        $type = [int]$adapter.InterfaceType
        if ($TunnelTypes -contains $type) {
            throw "LanHost porte une interface tunnel/VPN ($type) : la recette se sert sur le reseau local seulement"
        }
        return [pscustomobject]@{
            Address     = $Address
            Alias       = $adapter.InterfaceAlias
            Type        = $type
            Description = $adapter.InterfaceDescription
        }
    }

    # C43: a job that kills every process assigned to it when its last handle closes, i.e. when
    # this script ends for whatever reason (killed, window closed, Ctrl+C, end of the parent).
    function Enable-RecetteJob {
        if (-not ('DcRecetteJob' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;

public static class DcRecetteJob {
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimit {
        public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit;
        public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong R; public ulong W; public ulong O; public ulong RT; public ulong WT; public ulong OT; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimit {
        public BasicLimit Basic; public IoCounters Io; public UIntPtr ProcessMemoryLimit; public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed;
    }
    const uint KillOnJobClose = 0x2000;
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimit info, int length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, int length, IntPtr returned);

    // The handle is never closed by the script: the system closes it when the process ends.
    public static IntPtr CreateForCurrentProcess() {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        ExtendedLimit info = new ExtendedLimit();
        info.Basic.LimitFlags = KillOnJobClose;
        if (!SetInformationJobObject(job, 9, ref info, Marshal.SizeOf(typeof(ExtendedLimit)))) throw new Win32Exception();
        if (!AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle)) throw new Win32Exception();
        return job;
    }

    public static int[] ProcessIds(IntPtr job) {
        int capacity = 4096;
        int size = 8 + capacity * IntPtr.Size;
        IntPtr buffer = Marshal.AllocHGlobal(size);
        try {
            if (!QueryInformationJobObject(job, 3, buffer, size, IntPtr.Zero)) throw new Win32Exception();
            int count = Marshal.ReadInt32(buffer, 4);
            int[] ids = new int[count];
            for (int i = 0; i < count; i++) ids[i] = (int)Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size).ToInt64();
            return ids;
        }
        finally { Marshal.FreeHGlobal(buffer); }
    }
}
'@
        }
        try { return [DcRecetteJob]::CreateForCurrentProcess() }
        catch { throw "Objet job Windows indisponible (C43) : la recette ne se sert pas sans garantie d'arret. $($_.Exception.Message)" }
    }

    # The process that launched this script, by PID and start time (a reused PID is not it).
    function Get-ParentIdentity {
        $self = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction SilentlyContinue
        if (-not $self) { return $null }
        $parent = Get-Process -Id ([int]$self.ParentProcessId) -ErrorAction SilentlyContinue
        if (-not $parent) { return $null }
        try { return [pscustomobject]@{ Id = $parent.Id; Start = $parent.StartTime } } catch { return $null }
    }

    function Test-ParentAlive($Identity) {
        $live = Get-Process -Id $Identity.Id -ErrorAction SilentlyContinue
        if (-not $live) { return $false }
        try { return $live.StartTime -eq $Identity.Start } catch { return $false }
    }

    $lan = if ($LanHost) { Get-LanInterface $LanHost } else { Resolve-LanHost }
    $LanHost = $lan.Address
    Write-Host "RECETTE host $LanHost interface=$($lan.Alias) type=$($lan.Type) description=$($lan.Description)"
    if ($ResolveOnly) { exit 0 }
    $RunWeb = Set-TestRunWeb -RunManifest $RunManifest
    $Port = $RunWeb.app_port

    if (-not $ApiTarget) {
        $localEnv = Join-Path $RepoRoot 'infra/pve02/pve02.local.env'
        $line = if (Test-Path -LiteralPath $localEnv) {
            Get-Content -LiteralPath $localEnv |
                Where-Object { $_ -match '^WORKBASE_IP=' } | Select-Object -First 1
        }
        if (-not $line) { throw 'WORKBASE_IP absent de infra/pve02/pve02.local.env (gabarit : pve02.local.env.example)' }
        $ApiTarget = "http://$($line.Substring('WORKBASE_IP='.Length).Trim()):8000"
    }
    if ($ApiTarget -notmatch '^http://[\d.]+:\d+$') {
        throw "ApiTarget doit etre http://<ipv4>:<port> : $ApiTarget"
    }
    if ([string]$env:PUBLIC_DC_API_KEY -notmatch '^dc_anon_[A-Za-z0-9_-]{43,128}$') {
        throw 'PUBLIC_DC_API_KEY doit contenir la cle dc_anon_ de la seance (voir README, Procedure locale)'
    }

    # C43: every process started from here on lives in a job closed with this one.
    $Job = Enable-RecetteJob
    $parentWatch = Get-ParentIdentity

    $origin = "http://${LanHost}:$Port"
    $env:VITE_API_URL = $origin
    $env:DEV_API_PROXY_TARGET = $ApiTarget

    Write-Host "RECETTE serve $origin (API $ApiTarget)"
    Write-Host "  Ouvrir sur le telephone puis sur le PC portable : $origin/"
    Write-Host '  Arret : Ctrl+C (nettoyage de cette invocation), puis infra/pve02/workbase-recette.sh down'

    $exitCode = 0
    $LanBound = $LanHost
    $shell = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source
    $web = Start-Process -FilePath $shell -WorkingDirectory $FrontendDir -NoNewWindow -PassThru -ArgumentList @(
        '-NoProfile', '-File', ('"' + (Join-Path $PSScriptRoot 'start-test-web.ps1') + '"'),
        '-RunManifest', ('"' + $RunManifest + '"'), '-Kind', 'app', '-HostAddress', $LanHost)
    # Wait for the server, or for the end of the process that launched this script (C43).
    while (-not $web.WaitForExit(500)) {
        if ($parentWatch -and -not (Test-ParentAlive $parentWatch)) {
            Write-Host "RECETTE parent ended pid=$($parentWatch.Id): stopping"
            break
        }
    }
    if ($web.HasExited) { $exitCode = $web.ExitCode }
    $RunnerExit = $exitCode

}
catch {
    Write-Error $_ -ErrorAction Continue
    $RunnerExit = if ($LASTEXITCODE) { $LASTEXITCODE } else { 1 }
}
finally {
    $PriorRunnerExit = $RunnerExit
    $RunCleanupFailed = $false
    try {
        if ($RunInitialized -and $OwnTestRun) { Stop-TestRun -RunManifest $RunManifest }
        elseif ($RunInitialized) { Stop-TestRun -RunManifest $RunManifest -ScopeId $ResourceScope }
    }
    catch {
        $RunCleanupFailed = $true
        Write-Error $_ -ErrorAction Continue
    }
    finally { Restore-TestEnvironment $SavedRunEnvironment }
    if ($RunCleanupFailed) {
        if (-not $PriorRunnerExit) { $RunnerExit = 1 }
    }
    # C43: nothing this invocation started survives it, and nothing listens on <ip>:<port>.
    if ($Job -ne [IntPtr]::Zero) {
        try {
            Stop-RecetteJobProcesses $Job
            if ($LanBound -and $Port) {
                $listeners = Wait-NoListener -Address $LanBound -Port $Port -Seconds 15
                Write-Host "RECETTE stop listeners=$listeners host=$LanBound port=$Port"
                if ($listeners -and -not $RunnerExit) { $RunnerExit = 1 }
            }
        }
        catch {
            Write-Error $_ -ErrorAction Continue
            if (-not $RunnerExit) { $RunnerExit = 1 }
        }
    }
}

exit $RunnerExit

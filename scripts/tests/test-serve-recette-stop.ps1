<#
.SYNOPSIS
    C43 (WINDOWS n° 84 et 98) : serve-recette.ps1 ne laisse aucun ecouteur sur <ip>:<port> apres l'arret.

.DESCRIPTION
    Demarre le script en mode court (cle factice de la bonne forme, jamais provisionnee ; API
    cible sans reponse ; interface LAN retenue par -ResolveOnly), attend que vite ecoute sur
    <ip>:<port>, puis l'arrete de deux facons :
      - parent : fin du processus parent qui l'a lance (comme l'arret d'une tache par
        l'orchestrateur, TaskStop) ;
      - script : fin forcee du processus du script lui-meme (fenetre fermee, processus tue).
    Prouve ensuite qu'aucun ecouteur ne reste sur <ip>:<port> et qu'aucun processus de l'arbre
    lance ne survit. Ce qui survit est tue a la fin du test (taskkill /T), jamais laisse en place.

    Sortie 0 : les deux modes verts. Sortie 1 : un ecouteur ou un processus a survecu.
    Sortie 2 : aucune interface LAN candidate (non verifie, jamais vert).

.EXAMPLE
    pwsh -NoProfile -File scripts/tests/test-serve-recette-stop.ps1
#>
[CmdletBinding()]
param([ValidateSet('parent','script')][string[]]$Modes = @('parent','script'))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$Serve = Join-Path $RepoRoot 'scripts/serve-recette.ps1'
$Shell = (Get-Command pwsh -CommandType Application | Select-Object -First 1).Source

function Get-Descendants([int]$Root) {
    $all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CreationDate)
    $found = [System.Collections.Generic.List[object]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($Root)
    while ($queue.Count) {
        $current = $queue.Dequeue()
        foreach ($child in @($all | Where-Object { $_.ParentProcessId -eq $current })) {
            $found.Add($child)
            $queue.Enqueue([int]$child.ProcessId)
        }
    }
    return $found
}

function Get-Listeners([string]$Address, [int]$Port) {
    return @(Get-NetTCPConnection -State Listen -LocalAddress $Address -LocalPort $Port -ErrorAction SilentlyContinue)
}

# Whatever survived is killed, never left listening: each started process and its live tree.
function Stop-Lances {
    foreach ($process in @($lances)) {
        if (Test-Alive $process) {
            foreach ($child in @(Get-Descendants ([int]$process.ProcessId))) { $lances.Add($child) }
            & taskkill.exe /PID $process.ProcessId /T /F 2>&1 | Out-Null
        }
    }
}

function Test-Alive($Process) {
    $live = Get-CimInstance Win32_Process -Filter "ProcessId=$($Process.ProcessId)" -ErrorAction SilentlyContinue
    return [bool]($live -and $live.CreationDate -eq $Process.CreationDate)
}

# The interface the script itself retains, never all interfaces.
$resolve = & $Shell -NoProfile -File $Serve -ResolveOnly 2>&1 | ForEach-Object { "$_" }
$hostLine = $resolve | Where-Object { $_ -match '^RECETTE host (\d+\.\d+\.\d+\.\d+) ' } | Select-Object -First 1
if (-not $hostLine) {
    Write-Host "SERVE_RECETTE_STOP non verifie : aucune interface LAN candidate ($($resolve -join ' | '))"
    exit 2
}
$null = $hostLine -match '^RECETTE host (\d+\.\d+\.\d+\.\d+) '
$LanHost = $Matches[1]

$work = Join-Path ([IO.Path]::GetTempPath()) "dc-serve-recette-stop-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $work | Out-Null
$savedKey = $env:PUBLIC_DC_API_KEY
$failed = $false
# Everything this test starts, killed at the end whatever happens (an exception included).
$lances = [System.Collections.Generic.List[object]]::new()
$ports = [System.Collections.Generic.List[int]]::new()
try {
    foreach ($mode in $Modes) {
        # A key of the right shape, never provisioned anywhere: nothing is read with it.
        $bytes = [byte[]]::new(48)
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $env:PUBLIC_DC_API_KEY = 'dc_anon_' + ([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_'))
        $out = Join-Path $work "$mode.out.log"
        # The parent: a shell that runs the script as a child process, as Git Bash or the
        # orchestrator does (`pwsh -NoProfile -File scripts/serve-recette.ps1`).
        # Its output goes to a file directly (no pipe through the parent, as in a background task).
        $err = Join-Path $work "$mode.err.log"
        $command = "`$p = Start-Process -FilePath '$Shell' -ArgumentList '-NoProfile','-File','$Serve','-LanHost','$LanHost','-ApiTarget','http://127.0.0.1:9' " +
            "-NoNewWindow -PassThru -RedirectStandardOutput '$out' -RedirectStandardError '$err'; `$p.WaitForExit()"
        $parent = Start-Process -FilePath $Shell -ArgumentList @('-NoProfile','-Command', ('"' + $command.Replace('"','\"') + '"')) `
            -WindowStyle Hidden -PassThru
        $parentInfo = Get-CimInstance Win32_Process -Filter "ProcessId=$($parent.Id)"
        $lances.Add($parentInfo)
        $port = $null
        $deadline = [datetime]::UtcNow.AddSeconds(120)
        while ([datetime]::UtcNow -lt $deadline) {
            if (Test-Path $out) {
                $serveLine = Get-Content -LiteralPath $out -ErrorAction SilentlyContinue | Where-Object { $_ -match "^RECETTE serve http://$([regex]::Escape($LanHost)):(\d+) " } | Select-Object -First 1
                if ($serveLine) {
                    $null = $serveLine -match ':(\d+) '
                    $port = [int]$Matches[1]
                    if (-not $ports.Contains($port)) { $ports.Add($port) }
                    if (@(Get-Listeners $LanHost $port).Count) { break }
                }
            }
            if ($parent.HasExited) { break }
            Start-Sleep -Milliseconds 250
        }
        $tree = @(Get-Descendants $parent.Id)
        foreach ($process in $tree) { $lances.Add($process) }
        if (-not $port -or -not @(Get-Listeners $LanHost $port).Count) {
            Write-Host "SERVE_RECETTE_STOP mode=$mode demarrage en echec : $((Get-Content -LiteralPath $out -ErrorAction SilentlyContinue) -join ' | ')"
            $failed = $true
        }
        else {
            $script = $tree | Where-Object { $_.Name -eq 'pwsh.exe' -and $_.ParentProcessId -eq $parent.Id } | Select-Object -First 1
            if ($mode -eq 'parent') { Stop-Process -Id $parent.Id -Force }
            else { Stop-Process -Id $script.ProcessId -Force }
            $survivants = @()
            $ecouteurs = @()
            $deadline = [datetime]::UtcNow.AddSeconds(30)
            do {
                Start-Sleep -Milliseconds 500
                $survivants = @($tree | Where-Object { Test-Alive $_ })
                $ecouteurs = @(Get-Listeners $LanHost $port)
            } while (($survivants.Count -or $ecouteurs.Count) -and [datetime]::UtcNow -lt $deadline)
            $noms = ($survivants | ForEach-Object { "$($_.Name)#$($_.ProcessId)" }) -join ','
            $ok = -not $survivants.Count -and -not $ecouteurs.Count
            Write-Host "SERVE_RECETTE_STOP mode=$mode port=$port arbre=$($tree.Count) ecouteurs_apres=$($ecouteurs.Count) survivants=$($survivants.Count) [$noms] $(if ($ok) { 'ok' } else { 'ECHEC' })"
            if (-not $ok) { $failed = $true }
            $stopLine = Get-Content -LiteralPath $out -ErrorAction SilentlyContinue | Where-Object { $_ -match '^RECETTE (stop|parent)' }
            if ($stopLine) { Write-Host "  script: $($stopLine -join ' | ')" }
        }
        Stop-Lances
    }
}
finally {
    Stop-Lances
    foreach ($port in $ports) {
        if (@(Get-Listeners $LanHost $port).Count) {
            Write-Host "SERVE_RECETTE_STOP ecouteur encore present apres nettoyage du test sur ${LanHost}:$port"
            $failed = $true
        }
    }
    $env:PUBLIC_DC_API_KEY = $savedKey
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
if ($failed) { exit 1 }
exit 0

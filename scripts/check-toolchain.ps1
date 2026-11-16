#Requires -Version 7.0
<#
.SYNOPSIS
    Releve versionne des outils du poste et de l'emplacement des donnees Docker.

.DESCRIPTION
    Imprime une ligne `TOOL <nom> <version>` par outil de la chaine, puis trois
    lignes d'etat Docker :
      DockerRootDir    <chemin>  -- docker info --format '{{.DockerRootDir}}'
      DockerDataFolder <chemin>  -- cle `DataFolder` de %APPDATA%\Docker\settings-store.json,
                                    ou `default:%LOCALAPPDATA%\Docker\wsl` si la cle est absente
      DockerVhdxOnD    <true|false> -- presence de D:\Docker\wsl\disk\docker_data.vhdx

    Le script ne lit aucun secret et n'ecrit aucun fichier.

.PARAMETER RequireD
    Sort 1 si DockerVhdxOnD vaut false. Interrupteur optionnel : sans lui, le
    script sort 0 quel que soit l'emplacement des donnees Docker (la machine de
    test tourne sous la derogation D-13, Docker Desktop reste sur C:).

.EXAMPLE
    pwsh -NoProfile -File scripts/check-toolchain.ps1

.EXAMPLE
    pwsh -NoProfile -File scripts/check-toolchain.ps1 -RequireD
#>
[CmdletBinding()]
param(
    [switch]$RequireD
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Une sonde d'outil qui sort non nul signifie « version indisponible », pas
# « le releve a echoue » : sans cela, PowerShell 7.4+ transformerait chaque code
# de retour non nul en exception terminale.
$PSNativeCommandUseErrorActionPreference = $false

$GitleaksExe = Join-Path $env:USERPROFILE 'tools\gitleaks\gitleaks.exe'

function Get-ProbeText {
    <#
        Execute une sonde et rend sa premiere ligne utile, ou une chaine vide.
    #>
    param(
        [Parameter(Mandatory)][scriptblock]$Probe
    )

    $raw = $null
    try {
        $raw = & $Probe 2>$null
    } catch {
        return ''
    }

    if ($null -eq $raw) { return '' }
    $text = ($raw | Out-String)
    if ([string]::IsNullOrWhiteSpace($text)) { return '' }

    $first = $text -split "`r?`n" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -First 1
    return $first.Trim()
}

function Get-ToolVersion {
    <#
        Extrait le numero de version de la sortie d'une sonde.
        Rend `absent` quand l'outil ne repond pas.
    #>
    param(
        [Parameter(Mandatory)][scriptblock]$Probe
    )

    $text = Get-ProbeText -Probe $Probe
    if ($text -eq '') { return 'absent' }

    $match = [regex]::Match($text, '\d+\.\d+[\w.\-+]*')
    if ($match.Success) { return $match.Value }
    return $text
}

$tools = [ordered]@{
    'python'         = { python --version }
    'uv'             = { uv --version }
    'node'           = { node --version }
    'npm'            = { npm --version }
    'pwsh'           = { $PSVersionTable.PSVersion.ToString() }
    'git'            = { git --version }
    'docker'         = { docker info --format '{{.ServerVersion}}' }
    'docker-compose' = { docker compose version --short }
    'gitleaks'       = { & $GitleaksExe version }
}

foreach ($name in $tools.Keys) {
    Write-Output ('TOOL {0} {1}' -f $name, (Get-ToolVersion -Probe $tools[$name]))
}

# Emplacement des donnees Docker (D-13).
$dockerRootDir = Get-ProbeText -Probe { docker info --format '{{.DockerRootDir}}' }
if ($dockerRootDir -eq '') { $dockerRootDir = 'unknown' }
Write-Output ('DockerRootDir {0}' -f $dockerRootDir)

$settingsPath = Join-Path $env:APPDATA 'Docker\settings-store.json'
$dataFolder = ''
if (Test-Path -LiteralPath $settingsPath) {
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    $property = $settings.PSObject.Properties['DataFolder']
    if ($null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        $dataFolder = [string]$property.Value
    }
}
if ($dataFolder -eq '') {
    $dataFolder = 'default:' + (Join-Path $env:LOCALAPPDATA 'Docker\wsl')
}
Write-Output ('DockerDataFolder {0}' -f $dataFolder)

$vhdxOnD = Test-Path -LiteralPath 'D:\Docker\wsl\disk\docker_data.vhdx'
Write-Output ('DockerVhdxOnD {0}' -f $vhdxOnD.ToString().ToLowerInvariant())

if ($RequireD -and -not $vhdxOnD) {
    [Console]::Error.WriteLine('check-toolchain: -RequireD demande mais D:\Docker\wsl\disk\docker_data.vhdx est absent.')
    exit 1
}

exit 0

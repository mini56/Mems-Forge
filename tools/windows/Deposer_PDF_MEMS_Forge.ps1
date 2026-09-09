param()

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/mini56/Mems-Forge.git'

function Show-Message([string]$Text, [string]$Title = 'MEMS Forge') {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($Text, $Title) | Out-Null
}

function Find-Git {
    $cmd = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) {
        return [string]$cmd.Source
    }

    $desktopRoot = Join-Path $env:LOCALAPPDATA 'GitHubDesktop'
    if (Test-Path $desktopRoot) {
        $candidate = Get-ChildItem $desktopRoot -Directory -Filter 'app-*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'resources\app\git\cmd\git.exe' } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1

        if ($candidate) {
            return [string]$candidate
        }
    }

    throw 'Git est introuvable. Installe GitHub Desktop, connecte-toi a GitHub, puis relance cet outil.'
}

$Git = Find-Git
if (-not (Test-Path -LiteralPath $Git)) {
    throw "Git est introuvable au chemin detecte : $Git"
}

# Si l'outil est lance depuis un clone existant, utilise ce clone quel que soit son emplacement.
$scriptRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (Test-Path (Join-Path $scriptRepo '.git')) {
    $RepoDir = $scriptRepo
} else {
    # Mode autonome : cree le clone dans Documents si necessaire.
    $RepoDir = Join-Path $env:USERPROFILE 'Documents\Mems-Forge'
    if (-not (Test-Path (Join-Path $RepoDir '.git'))) {
        $parent = Split-Path $RepoDir -Parent
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
        & $Git clone $RepoUrl $RepoDir
        if ($LASTEXITCODE -ne 0) { throw 'Impossible de cloner le depot Mems-Forge.' }
    }
}

Set-Location $RepoDir

# Met a jour le depot avant le depot du nouveau PDF.
& $Git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw 'Impossible de mettre a jour le depot. Aucun PDF n''a ete envoye.' }

# Git LFS doit etre disponible pour les gros PDF.
& $Git lfs version *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Git LFS est introuvable. Reinstalle ou mets a jour GitHub Desktop, puis relance cet outil.'
}
& $Git lfs install --local *> $null

Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = 'Choisir le PDF a envoyer a MEMS Forge'
$dialog.Filter = 'Fichiers PDF (*.pdf)|*.pdf'
$dialog.Multiselect = $false

if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
    exit 0
}

$source = $dialog.FileName
if ([IO.Path]::GetExtension($source).ToLowerInvariant() -ne '.pdf') {
    throw 'Le fichier choisi n''est pas un PDF.'
}

# Chaque depot garde le nom original mais vit dans un dossier unique.
# Cela evite qu'un PDF portant le meme nom qu'un ancien ecrase sa revision precedente.
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$depositDir = Join-Path $RepoDir (Join-Path 'depot' $stamp)
New-Item -ItemType Directory -Force -Path $depositDir | Out-Null
$destination = Join-Path $depositDir ([IO.Path]::GetFileName($source))
Copy-Item -LiteralPath $source -Destination $destination -Force

$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $destination).Length
$relative = $destination.Substring($RepoDir.Length + 1).Replace('\','/')

& $Git add -- $relative
if ($LASTEXITCODE -ne 0) { throw 'Impossible de preparer le PDF pour Git.' }

$status = & $Git status --porcelain -- $relative
if (-not $status) {
    throw 'Git ne detecte aucun nouveau PDF a envoyer.'
}

$commitMessage = "Depot PDF MEMS Forge: $([IO.Path]::GetFileName($source))"
& $Git commit -m $commitMessage
if ($LASTEXITCODE -ne 0) { throw 'Le commit du PDF a echoue.' }

& $Git push origin main
if ($LASTEXITCODE -ne 0) {
    throw 'Le PDF a ete copie et commit localement, mais le push GitHub a echoue. Ouvre GitHub Desktop pour verifier la connexion, puis relance.'
}

Show-Message ("PDF envoye a MEMS Forge.`n`nFichier : {0}`nTaille : {1:N0} octets`nSHA-256 : {2}`n`nLe run GitHub Actions va demarrer automatiquement." -f ([IO.Path]::GetFileName($source)), $size, $hash) 'MEMS Forge - Depot termine'

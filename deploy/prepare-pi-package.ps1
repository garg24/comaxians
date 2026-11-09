<#
  Prépare, sur Windows, l'archive à copier sur le Raspberry Pi.

  Utilisation (depuis le dossier ComAxians) :
    powershell -ExecutionPolicy Bypass -File deploy\prepare-pi-package.ps1            # application seule
    powershell -ExecutionPolicy Bypass -File deploy\prepare-pi-package.ps1 -WithData  # + vos contenus actuels

  Résultat : dist\comaxians-pi.tar.gz
#>
param([switch]$WithData)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root 'dist'
New-Item -ItemType Directory -Force $dist | Out-Null
$out = Join-Path $dist 'comaxians-pi.tar.gz'

$items = @('app', 'run.py', 'requirements.txt', 'deploy', 'README.md')
if ($WithData) {
    if (-not (Test-Path (Join-Path $root 'data\comaxians.db'))) { throw "Aucune donnée trouvée dans $root\data" }
    $items += 'data'
}

# Les scripts shell doivent garder des fins de ligne Unix (LF)
Get-ChildItem (Join-Path $root 'deploy') -Filter *.sh | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName)
    if ($text.Contains("`r`n")) {
        [IO.File]::WriteAllText($_.FullName, $text.Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
    }
}

Push-Location $root
try {
    & tar.exe --exclude='__pycache__' --exclude='.venv' --exclude='*.pyc' -czf $out @items
    if ($LASTEXITCODE -ne 0) { throw "tar a échoué ($LASTEXITCODE)" }
} finally { Pop-Location }

$size = [math]::Round((Get-Item $out).Length / 1MB, 2)
Write-Host ""
Write-Host "Archive prête : $out ($size Mo)" -ForegroundColor Green
if ($WithData) { Write-Host "Elle contient vos contenus actuels (base + images)." }
Write-Host ""
Write-Host "Cette archive sert au Raspberry Pi qui héberge le serveur (« serveur + écran » ou « serveur seul »)."
Write-Host "Les écrans n'en ont pas besoin :"
Write-Host "ils s'installent avec une seule commande, affichée dans l'admin (section Écrans)."
Write-Host ""
Write-Host "Étapes suivantes (remplacez pi@IP-DU-PI par l'utilisateur et l'adresse de votre Raspberry Pi) :"
Write-Host "  scp `"$out`" pi@IP-DU-PI:~/"
Write-Host "  ssh pi@IP-DU-PI"
Write-Host "  mkdir -p ~/comaxians && tar -xzf ~/comaxians-pi.tar.gz -C ~/comaxians"
Write-Host "  bash ~/comaxians/deploy/install-serveur-et-ecran.sh --screen accueil   # serveur + écran"
Write-Host "  sudo reboot"
Write-Host "  (serveur seul, sans écran sur ce Pi : bash ~/comaxians/deploy/install-serveur.sh, sans redémarrage)"

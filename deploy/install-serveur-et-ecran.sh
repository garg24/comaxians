#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : installation TOUT-EN-UN (serveur + écran) sur un Raspberry Pi
#
#   1. le SERVEUR (administration + diffusion) : délégué à install-serveur.sh (service systemd
#      démarré au boot, données dans /var/lib/comaxians, sauvegarde quotidienne) ;
#   2. l'ÉCRAN de ce même Pi : délégué à install-ecran.sh (kiosque sur http://localhost:8080).
#
# Serveur seul (sans écran sur ce Pi) : install-serveur.sh. Les autres écrans n'ont besoin que de
# install-ecran.sh (voir le résumé en fin d'installation).
# Relancer ce script met à jour l'application en conservant les données et le mot de passe.
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR=/var/backups/comaxians
PORT=8080

SCREEN_NAME=""
DRY_RUN=0
SERVER_OPTS=()     # options transmises telles quelles à install-serveur.sh
SCREEN_OPTS=()     # options transmises telles quelles à install-ecran.sh

usage() {
  cat <<'EOF'
Installation tout-en-un Axians (serveur + écran sur le même Raspberry Pi)

  bash deploy/install-serveur-et-ecran.sh [options]

Options :
  --screen NOM             nom de l'écran de ce Pi (ciblage des contenus, thème dans l'admin)
  --full-animations        garder toutes les animations (par défaut : allégées sur Pi 3)
  --resolution R           720p | 1080p | keep (par défaut : 720p sur Pi 3, inchangée sinon)
  --import-data DOSSIER    reprendre des données existantes (base + images) – 1re installation
  --no-import              ne pas reprendre le dossier data/ livré avec l'application
  --dry-run                simulation : affiche ce qui serait fait, sans rien modifier
  -h, --help               cette aide

Mot de passe admin : demandé à la 1re installation (ou variable COMAXIANS_ADMIN_PASSWORD).
Serveur seul, sans écran sur ce Pi : deploy/install-serveur.sh.
EOF
  exit "${1:-0}"
}

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }

main() {
while [[ $# -gt 0 ]]; do
  case $1 in
    --screen)          SCREEN_NAME=${2:?nom manquant}; SCREEN_OPTS+=(--screen "$2"); shift 2 ;;
    --full-animations) SCREEN_OPTS+=(--full-animations); shift ;;
    --resolution)      SCREEN_OPTS+=(--resolution "${2:?valeur manquante}"); shift 2 ;;
    --import-data)     SERVER_OPTS+=(--import-data "${2:?dossier manquant}"); shift 2 ;;
    --no-import)       SERVER_OPTS+=(--no-import); shift ;;
    --dry-run)         DRY_RUN=1; shift ;;
    -h|--help)         usage 0 ;;
    *)                 printf 'Option inconnue : %s\n\n' "$1"; usage 1 ;;
  esac
done
# Vérifié avant de commencer : une erreur côté écran ne doit pas arriver après l'installation du serveur
if [[ -n $SCREEN_NAME && ! $SCREEN_NAME =~ ^[A-Za-z0-9_-]{1,40}$ ]]; then
  die "nom d'écran invalide (lettres, chiffres, - et _ uniquement, 40 caractères max)"
fi
if [[ $DRY_RUN == 1 ]]; then SERVER_OPTS+=(--dry-run); SCREEN_OPTS+=(--dry-run); fi

# -------------------------------------------------------------- vérifications
[[ -f $SRC_DIR/run.py && -d $SRC_DIR/app ]] || die "fichiers de l'application introuvables dans $SRC_DIR (extrayez l'archive complète, puis lancez deploy/install-serveur-et-ecran.sh)"
[[ -f $SRC_DIR/deploy/install-serveur.sh ]] || die "deploy/install-serveur.sh introuvable"
[[ -f $SRC_DIR/deploy/install-ecran.sh ]] || die "deploy/install-ecran.sh introuvable"
if [[ $EUID -eq 0 && $DRY_RUN == 0 ]]; then
  die "lancez ce script avec l'utilisateur qui ouvre la session graphique (pas avec sudo) : il demandera sudo lui-même"
fi

log "Installation tout-en-un (serveur + écran) – étape 1/2 : serveur"
# (le mot de passe admin éventuel est demandé par install-serveur.sh, qui hérite du clavier)
bash "$SRC_DIR/deploy/install-serveur.sh" --no-summary "${SERVER_OPTS[@]}"

log "[Écran] Configuration de l'affichage de ce Raspberry Pi (étape 2/2)"
bash "$SRC_DIR/deploy/install-ecran.sh" --server "localhost:$PORT" "${SCREEN_OPTS[@]}"

# ----------------------------------------------------------------- résumé
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP=${IP:-ADRESSE-DU-PI}
cat <<EOF

$(printf '\033[1;32m')Installation tout-en-un terminée.$(printf '\033[0m')

  Administration (depuis votre PC) : http://$IP:$PORT/admin

  À faire maintenant :
   1. Réservez l'adresse $IP pour ce Raspberry Pi dans votre box / routeur (réservation DHCP).
   2. Redémarrez :  sudo reboot

  Ajouter un autre écran : sur le nouveau Raspberry Pi, une seule commande suffit
     curl -fsSL http://$IP:$PORT/install-ecran.sh | bash -s -- --server $IP --screen NOM
  (box Android / Android TV : application « Axians Affichage », voir le README)

  Commandes utiles :
   - état du serveur       : sudo systemctl status comaxians
   - journal du serveur    : journalctl -u comaxians -f
   - sauvegarde immédiate  : sudo comaxians-backup
   - revenir en arrière    : sudo comaxians-restore   (liste les sauvegardes, puis restaure celle choisie)
   - sauvegardes           : $BACKUP_DIR  (copiez-les régulièrement hors du Pi)
   - mise à jour           : curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash
                             (vos contenus sont conservés et sauvegardés juste avant)
   - tout désinstaller     : bash $SRC_DIR/deploy/desinstaller.sh

EOF
}

main "$@"

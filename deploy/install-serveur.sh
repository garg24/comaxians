#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : installation du SERVEUR SEUL sur un Raspberry Pi
#
# Administration + diffusion des contenus : service systemd démarré au boot, données dans
# /var/lib/comaxians, sauvegarde quotidienne. Aucun écran n'est configuré sur ce Pi
# (il peut fonctionner sans écran, avec Raspberry Pi OS Lite).
#
# Les écrans se branchent ensuite sur ce serveur :
#   - Raspberry Pi : install-ecran.sh (commande donnée en fin d'installation) ;
#   - box Android / Android TV : application « Axians Affichage » (APK).
# Ce script est aussi utilisé par install-serveur-et-ecran.sh (rôle serveur du tout-en-un).
# Relancer ce script met à jour l'application en conservant les données et le mot de passe.
# =============================================================================
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/comaxians
DATA_DIR=/var/lib/comaxians
ENV_FILE=/etc/comaxians.env
UNIT_FILE=/etc/systemd/system/comaxians.service
BACKUP_BIN=/usr/local/bin/comaxians-backup
RESTORE_BIN=/usr/local/bin/comaxians-restore
BACKUP_DIR=/var/backups/comaxians
UPDATE_BIN=/usr/local/bin/comaxians-update
UPDATE_CONF=/etc/comaxians-update.conf
SERVICE_USER=comaxians
PORT=8080
APK_URL=https://github.com/garg24/comaxians/releases/latest/download/comaxians-ecran.apk
INSTALL_URL=https://raw.githubusercontent.com/garg24/comaxians/main/install.sh

IMPORT_DATA=""
NO_IMPORT=0
DRY_RUN=0
SUMMARY=1

usage() {
  cat <<'EOF'
Installation du serveur Axians seul (administration + diffusion, sans écran sur ce Pi)

  bash deploy/install-serveur.sh [options]

Options :
  --import-data DOSSIER    reprendre des données existantes (base + images) – 1re installation
  --no-import              ne pas reprendre le dossier data/ livré avec l'application
  --dry-run                simulation : affiche ce qui serait fait, sans rien modifier
  -h, --help               cette aide

Mot de passe admin : demandé à la 1re installation (ou variable COMAXIANS_ADMIN_PASSWORD).
Serveur + écran sur le même Pi : utilisez plutôt deploy/install-serveur-et-ecran.sh.
EOF
  exit "${1:-0}"
}

# ----------------------------------------------------------------- utilitaires
log()  { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m    ATTENTION : %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
run() {
  if [[ $DRY_RUN == 1 ]]; then printf '    [simulation] %s\n' "$*"; else "$@"; fi
}
# Écrit un fichier système (contenu lu sur l'entrée standard) avec des droits donnés
write_file() {   # write_file CHEMIN MODE [PROPRIETAIRE]
  local path=$1 mode=$2 owner=${3:-root:root}
  if [[ $DRY_RUN == 1 ]]; then
    printf '    [simulation] écriture de %s (%s, %s) :\n' "$path" "$mode" "$owner"
    sed -E 's/(PASSWORD=).*/\1"********"/; s/^/        | /'
  else
    sudo tee "$path" >/dev/null
    sudo chmod "$mode" "$path"
    sudo chown "$owner" "$path"
  fi
}

# Données déjà présentes sur ce Pi ? (le dossier n'est lisible que par root et le service :
# un simple [[ -f ]] lancé avec l'utilisateur normal répondrait « non » à tort)
data_exists() {
  # (en simulation, AXIANS_DRYRUN_DATA_DIR permet de tester le cas « mise à jour » sans rien installer)
  if [[ $DRY_RUN == 1 ]]; then [[ -f ${AXIANS_DRYRUN_DATA_DIR:-$DATA_DIR}/comaxians.db ]] 2>/dev/null
  else sudo test -f "$DATA_DIR/comaxians.db"; fi
}

# Outils de sauvegarde / restauration + sauvegarde quotidienne programmée
install_backup_tools() {
  write_file "$BACKUP_BIN" 755 <<'EOF'
#!/usr/bin/env bash
# Sauvegarde des données Axians (base SQLite copiée à chaud + images).
#   sudo comaxians-backup                                   → sauvegarde datée (conservée 14 jours)
#   sudo env COMAXIANS_BACKUP_LABEL=avant-maj comaxians-backup → sauvegarde nommée (conservée 90 jours)
set -euo pipefail
DATA_DIR=${COMAXIANS_DATA_DIR:-/var/lib/comaxians}
DEST=${COMAXIANS_BACKUP_DIR:-/var/backups/comaxians}
KEEP_DAYS=${COMAXIANS_BACKUP_KEEP:-14}
LABEL=${COMAXIANS_BACKUP_LABEL:-}
PYTHON=/opt/comaxians/.venv/bin/python
[[ -x $PYTHON ]] || PYTHON=python3
mkdir -p "$DEST"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
"$PYTHON" - "$DATA_DIR/comaxians.db" "$TMP/comaxians.db" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
[[ -d $DATA_DIR/uploads ]] && cp -a "$DATA_DIR/uploads" "$TMP/"
[[ -f $DATA_DIR/.secret_key ]] && cp -a "$DATA_DIR/.secret_key" "$TMP/"
ARCHIVE="$DEST/comaxians-${LABEL:+$LABEL-}$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$ARCHIVE" -C "$TMP" .
chmod 640 "$ARCHIVE"
chgrp adm "$ARCHIVE" 2>/dev/null || true
# Rotation : sauvegardes quotidiennes 14 jours, sauvegardes nommées (avant mise à jour…) 90 jours
find "$DEST" -name 'comaxians-[0-9]*.tar.gz' -mtime +"$KEEP_DAYS" -delete
find "$DEST" -name 'comaxians-[a-z]*.tar.gz' -mtime +90 -delete
echo "Sauvegarde : $ARCHIVE"
EOF

  write_file "$RESTORE_BIN" 755 <<'EOF'
#!/usr/bin/env bash
# Restauration des données Axians depuis une sauvegarde.
#   sudo comaxians-restore                 → liste les sauvegardes disponibles
#   sudo comaxians-restore FICHIER.tar.gz  → restaure (l'état actuel est d'abord sauvegardé)
set -euo pipefail
DATA_DIR=${COMAXIANS_DATA_DIR:-/var/lib/comaxians}
DEST=${COMAXIANS_BACKUP_DIR:-/var/backups/comaxians}
SERVICE_USER=${COMAXIANS_SERVICE_USER:-comaxians}
BACKUP_BIN=${COMAXIANS_BACKUP_BIN:-/usr/local/bin/comaxians-backup}
SERVICE=${COMAXIANS_SERVICE-comaxians}       # définie mais vide = ne pas arrêter/relancer de service (tests)
if [[ $EUID -ne 0 && -z ${COMAXIANS_ALLOW_NONROOT:-} ]]; then echo "À lancer avec sudo." >&2; exit 1; fi
if [[ $# -eq 0 ]]; then
  echo "Sauvegardes disponibles (la plus récente en dernier) :"
  ls -1tr "$DEST"/comaxians-*.tar.gz 2>/dev/null | sed 's/^/  /' || echo "  (aucune)"
  echo; echo "Restaurer : sudo comaxians-restore CHEMIN_DE_LA_SAUVEGARDE"
  exit 0
fi
ARCHIVE=$1
[[ -f $ARCHIVE ]] || { echo "Sauvegarde introuvable : $ARCHIVE" >&2; exit 1; }
CONTENT=$(tar -tzf "$ARCHIVE") || { echo "Archive illisible : $ARCHIVE" >&2; exit 1; }
grep -qxE '(\./)?comaxians\.db' <<<"$CONTENT" || { echo "Ce fichier ne contient pas de base Axians." >&2; exit 1; }
echo "1/4 Sauvegarde de l'état actuel (filet de sécurité)…"
COMAXIANS_BACKUP_LABEL=avant-restauration "$BACKUP_BIN"
echo "2/4 Arrêt du serveur…"
[[ -n $SERVICE ]] && systemctl stop "$SERVICE"
echo "3/4 Restauration de $ARCHIVE…"
rm -rf "$DATA_DIR/uploads" "$DATA_DIR/comaxians.db" "$DATA_DIR"/comaxians.db-*
tar -xzf "$ARCHIVE" -C "$DATA_DIR"
id "$SERVICE_USER" &>/dev/null && chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
echo "4/4 Redémarrage du serveur…"
[[ -n $SERVICE ]] && systemctl start "$SERVICE"
echo "Restauration terminée. Les écrans se mettent à jour d'eux-mêmes en moins d'une minute."
EOF

  run sudo mkdir -p "$BACKUP_DIR"
  run sudo chown root:adm "$BACKUP_DIR"     # lisible par l'utilisateur principal (groupe adm)
  run sudo chmod 750 "$BACKUP_DIR"
  write_file /etc/cron.d/comaxians-backup 644 <<EOF
# Sauvegarde quotidienne des données Axians
30 3 * * * root $BACKUP_BIN >/dev/null 2>&1
EOF
}

# Tout le script est dans une fonction appelée à la fin : bash le lit en entier avant de l'exécuter.
main() {
while [[ $# -gt 0 ]]; do
  case $1 in
    --import-data) IMPORT_DATA=${2:?dossier manquant}; shift 2 ;;
    --no-import)   NO_IMPORT=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --no-summary)  SUMMARY=0; shift ;;     # utilisé par install-serveur-et-ecran.sh
    -h|--help)     usage 0 ;;
    *)             printf 'Option inconnue : %s\n\n' "$1"; usage 1 ;;
  esac
done

# -------------------------------------------------------------- vérifications
[[ -f $SRC_DIR/run.py && -d $SRC_DIR/app ]] || die "fichiers de l'application introuvables dans $SRC_DIR (extrayez l'archive complète, puis lancez deploy/install-serveur.sh)"
[[ -f $SRC_DIR/deploy/install-ecran.sh ]] || die "deploy/install-ecran.sh introuvable"
[[ -f $SRC_DIR/deploy/comaxians-update.sh ]] || die "deploy/comaxians-update.sh introuvable"
# Lancé en root sur un système sans sudo (image minimale) : les commandes sont exécutées telles quelles
if [[ $EUID -eq 0 ]] && ! command -v sudo >/dev/null; then sudo() { "$@"; }; fi

MODEL=$(tr -d '\0' 2>/dev/null </proc/device-tree/model || echo "modèle inconnu")
log "Installation du serveur Axians – $MODEL"
info "Application source : $SRC_DIR"
[[ $DRY_RUN == 1 ]] && warn "MODE SIMULATION : rien ne sera modifié"

# ------------------------------------------------------------ mot de passe admin
PASSWORD=""
KEEP_ENV=0
if [[ -n ${COMAXIANS_ADMIN_PASSWORD:-} ]]; then
  PASSWORD=$COMAXIANS_ADMIN_PASSWORD
elif [[ $DRY_RUN == 0 ]] && sudo test -f "$ENV_FILE"; then
  KEEP_ENV=1
  info "Mot de passe admin existant conservé (pour le changer : relancez avec COMAXIANS_ADMIN_PASSWORD=...)"
elif [[ $DRY_RUN == 1 ]]; then
  PASSWORD="motdepasse-simulation"
else
  echo
  echo "Choisissez le mot de passe de l'administration (8 caractères minimum) :"
  while :; do
    read -rsp "  Mot de passe : " p1; echo
    read -rsp "  Confirmation : " p2; echo
    if [[ $p1 != "$p2" ]]; then echo "  Les deux saisies diffèrent, recommencez."
    elif (( ${#p1} < 8 )); then echo "  Trop court (8 caractères minimum)."
    elif [[ $p1 == "axians" ]]; then echo "  Choisissez autre chose que le mot de passe par défaut."
    else PASSWORD=$p1; break; fi
  done
fi

# --------------------------------------------------------- installation
UPDATE=0
data_exists && UPDATE=1
if [[ $UPDATE == 1 ]]; then
  info "Mise à jour d'une installation existante : vos contenus seront conservés."
fi

log "[Serveur] Outils de sauvegarde et de restauration"
install_backup_tools
if [[ $UPDATE == 1 ]]; then
  log "[Serveur] Sauvegarde automatique avant mise à jour"
  # En cas d'échec de la sauvegarde, l'installation s'arrête (set -e) : rien n'est modifié
  run sudo env COMAXIANS_BACKUP_LABEL=avant-maj "$BACKUP_BIN"
fi

log "[Serveur] Paquets système (Python, outils)"
run sudo apt-get update -y
run sudo apt-get install -y python3-venv python3-pip curl rsync

log "[Serveur] Utilisateur de service « $SERVICE_USER »"
if id "$SERVICE_USER" &>/dev/null; then
  info "déjà présent"
else
  run sudo useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

log "[Serveur] Copie de l'application dans $APP_DIR"
run sudo mkdir -p "$APP_DIR/deploy" "$DATA_DIR"
# --delete supprime les anciens fichiers de l'application ; .venv est protégé par --exclude
run sudo rsync -a --delete --exclude '.venv' --exclude '__pycache__' \
  "$SRC_DIR/app" "$SRC_DIR/run.py" "$SRC_DIR/requirements.txt" "$APP_DIR/"
# Le script « écran » est servi par le serveur (http://…:8080/install-ecran.sh) pour équiper les Pi écrans
run sudo install -m 644 "$SRC_DIR/deploy/install-ecran.sh" "$APP_DIR/deploy/install-ecran.sh"
# Version installée (commit GitHub), affichée dans l'admin pour savoir si une mise à jour existe
VERSION=$(git -C "$SRC_DIR" rev-parse HEAD 2>/dev/null || echo "inconnue")
printf '%s\n' "$VERSION" | write_file "$APP_DIR/VERSION" 644
info "version installée : ${VERSION:0:7}"
run sudo chown -R root:root "$APP_DIR"   # le service ne peut pas modifier son propre code

log "[Serveur] Environnement Python"
if [[ -x $APP_DIR/.venv/bin/python ]]; then info "environnement existant réutilisé"
else run sudo python3 -m venv "$APP_DIR/.venv"; fi
run sudo "$APP_DIR/.venv/bin/pip" install --quiet --disable-pip-version-check --upgrade -r "$APP_DIR/requirements.txt"

log "[Serveur] Données (base + images) dans $DATA_DIR"
if [[ $UPDATE == 1 ]]; then
  info "données existantes conservées (le dossier data/ de l'archive, s'il existe, est ignoré)"
  [[ -n $IMPORT_DATA ]] && warn "--import-data ignoré : des données existent déjà (pour revenir à une sauvegarde : sudo comaxians-restore)"
else
  if [[ -z $IMPORT_DATA && $NO_IMPORT == 0 && -f $SRC_DIR/data/comaxians.db ]]; then
    IMPORT_DATA=$SRC_DIR/data
  fi
  if [[ -n $IMPORT_DATA ]]; then
    [[ -f $IMPORT_DATA/comaxians.db ]] || die "aucune base comaxians.db dans $IMPORT_DATA"
    info "reprise des données de $IMPORT_DATA"
    run sudo cp -a "$IMPORT_DATA/." "$DATA_DIR/"
  else
    info "nouvelle installation : contenus d'exemple créés au premier démarrage"
  fi
fi
run sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$DATA_DIR"
run sudo chmod 750 "$DATA_DIR"

log "[Serveur] Configuration ($ENV_FILE, lisible par root uniquement)"
if [[ $KEEP_ENV == 1 ]]; then
  info "fichier existant conservé"
else
  # Format systemd : valeur entre guillemets, \ et " échappés
  esc=${PASSWORD//\\/\\\\}; esc=${esc//\"/\\\"}
  {
    printf '# Généré par install-serveur.sh\n'
    printf 'COMAXIANS_ADMIN_PASSWORD="%s"\n' "$esc"
    printf 'COMAXIANS_DATA_DIR=%s\n' "$DATA_DIR"
    printf 'PORT=%s\n' "$PORT"
  } | write_file "$ENV_FILE" 600
fi

log "[Serveur] Service systemd « comaxians » (démarrage automatique)"
write_file "$UNIT_FILE" 644 <<EOF
[Unit]
Description=Axians - affichage dynamique (serveur)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/python run.py
Restart=always
RestartSec=3
# Priorité légèrement plus basse : si un écran tourne sur le même Pi, il reste fluide
Nice=5
# Durcissement : le service n'écrit que dans son dossier de données
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
run sudo systemctl daemon-reload
run sudo systemctl enable comaxians
run sudo systemctl restart comaxians

if [[ $DRY_RUN == 0 ]]; then
  info "attente du démarrage du serveur…"
  ok=0
  for _ in $(seq 1 40); do
    if curl -fs -o /dev/null "http://localhost:$PORT/display"; then ok=1; break; fi
    sleep 1
  done
  if [[ $ok == 0 ]]; then
    sudo journalctl -u comaxians -n 30 --no-pager || true
    die "le serveur ne répond pas sur le port $PORT (voir le journal ci-dessus)"
  fi
  info "serveur opérationnel ✔"
fi

log "[Serveur] Bouton « Mettre à jour » de l'admin"
# Le serveur web (sans droits) dépose une demande ; systemd lance alors la mise à jour en root
SRC_USER=$(stat -c %U "$SRC_DIR" 2>/dev/null || id -un)
{
  printf '# Généré par install-serveur.sh : code source utilisé par la mise à jour depuis l'"'"'admin\n'
  printf 'SRC_DIR=%q\nSRC_USER=%q\n' "$SRC_DIR" "$SRC_USER"
} | write_file "$UPDATE_CONF" 644
# « install » remplace le fichier (nouveau fichier) : sans risque même pendant une mise à jour en cours
run sudo install -m 755 "$SRC_DIR/deploy/comaxians-update.sh" "$UPDATE_BIN"
write_file /etc/systemd/system/comaxians-update.service 644 <<EOF
[Unit]
Description=Axians - mise à jour demandée depuis l'admin

[Service]
Type=oneshot
ExecStart=$UPDATE_BIN
TimeoutStartSec=30min
EOF
write_file /etc/systemd/system/comaxians-update.path 644 <<EOF
[Unit]
Description=Axians - surveillance des demandes de mise à jour

[Path]
PathExists=$DATA_DIR/update-request
Unit=comaxians-update.service

[Install]
WantedBy=multi-user.target
EOF
run sudo systemctl daemon-reload
run sudo systemctl enable --now comaxians-update.path
if [[ ! -d $SRC_DIR/.git ]]; then
  info "installation par archive : le bouton de l'admin indiquera les nouvelles versions, la mise à jour se fera à la main"
fi

if [[ $UPDATE == 0 ]]; then
  log "[Serveur] Première sauvegarde (puis chaque jour à 3 h 30) dans $BACKUP_DIR"
  run sudo "$BACKUP_BIN"
fi

[[ $SUMMARY == 1 ]] || return 0

# ----------------------------------------------------------------- résumé
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP=${IP:-ADRESSE-DU-PI}
cat <<EOF

$(printf '\033[1;32m')Installation du serveur terminée.$(printf '\033[0m')

  Administration (depuis votre PC) : http://$IP:$PORT/admin
  Aperçu de l'affichage            : http://$IP:$PORT/display

  À faire maintenant :
   Réservez l'adresse $IP pour ce Raspberry Pi dans votre box / routeur (réservation DHCP) :
   c'est l'adresse que les écrans utilisent pour se connecter.

  Brancher des écrans sur ce serveur :
   - Raspberry Pi : sur chaque Pi écran, une seule commande
       curl -fsSL http://$IP:$PORT/install-ecran.sh | bash -s -- --server $IP --screen NOM
   - Box Android / Android TV : application « Axians Affichage »
       $APK_URL
       (adresse du serveur à saisir dans l'application : $IP)

  Commandes utiles :
   - état du serveur       : sudo systemctl status comaxians
   - journal du serveur    : journalctl -u comaxians -f
   - sauvegarde immédiate  : sudo comaxians-backup
   - revenir en arrière    : sudo comaxians-restore   (liste les sauvegardes, puis restaure celle choisie)
   - sauvegardes           : $BACKUP_DIR  (copiez-les régulièrement hors du Pi)
   - mise à jour           : curl -fsSL $INSTALL_URL | bash -s -- --serveur-seul
                             (vos contenus sont conservés et sauvegardés juste avant)
   - tout désinstaller     : bash $SRC_DIR/deploy/desinstaller.sh

EOF
}

main "$@"

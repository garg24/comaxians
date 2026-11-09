#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : DÉSINSTALLATION d'un Raspberry Pi
#
# Retire ce qu'ont installé install-serveur-et-ecran.sh, install-serveur.sh et/ou install-ecran.sh
# (les rôles présents sur ce Pi sont détectés automatiquement).
#   bash desinstaller.sh          → supprime l'application ; vos contenus sont d'abord sauvegardés
#                                   dans /var/backups/comaxians (conservé)
#   bash desinstaller.sh --tout   → supprime AUSSI toutes les sauvegardes et copies (irréversible)
#
# Directement depuis GitHub :
#   curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/deploy/desinstaller.sh | bash
# =============================================================================
set -euo pipefail

# AXIANS_TEST_PREFIX : uniquement pour les tests automatisés (chemins système dans un dossier de test)
P=${AXIANS_TEST_PREFIX:-}
APP_DIR=$P/opt/comaxians
DATA_DIR=$P/var/lib/comaxians
ENV_FILE=$P/etc/comaxians.env
UNIT_FILE=$P/etc/systemd/system/comaxians.service
CRON_BACKUP=$P/etc/cron.d/comaxians-backup
BACKUP_BIN=$P/usr/local/bin/comaxians-backup
RESTORE_BIN=$P/usr/local/bin/comaxians-restore
BACKUP_DIR=$P/var/backups/comaxians
SERVICE_USER=comaxians
CMDLINES=("$P/boot/firmware/cmdline.txt" "$P/boot/cmdline.txt")

KIOSK=$HOME/axians-kiosk.sh
AUTOSTART=$HOME/.config/autostart/axians-kiosk.desktop
LABWC=$HOME/.config/labwc/autostart
SRC=${COMAXIANS_SRC:-$HOME/comaxians}

DO_SERVER=1
DO_SCREEN=1
PURGE=0
YES=0
DRY_RUN=0

usage() {
  cat <<'EOF'
Désinstallation Axians (Raspberry Pi)

  bash desinstaller.sh [options]

Options :
  --tout        supprime aussi les sauvegardes (/var/backups/comaxians) et les copies
                (~/comaxians-data, ~/comaxians.ancien-*, ~/comaxians-pi.tar.gz) – IRRÉVERSIBLE
  --serveur     ne retire que le rôle serveur (l'écran de ce Pi reste configuré)
  --ecran       ne retire que le rôle écran (le serveur continue de tourner)
  --oui         ne pas demander de confirmation
  --dry-run     simulation : affiche ce qui serait fait, sans rien modifier
  -h, --help    cette aide

Sans --tout, une sauvegarde de vos contenus est faite juste avant la suppression ; après une
réinstallation, elle se restaure avec : sudo comaxians-restore
EOF
  exit "${1:-0}"
}

log()  { printf '\n\033[1;31m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m    ATTENTION : %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
run()  { if [[ $DRY_RUN == 1 ]]; then printf '    [simulation] %s\n' "$*"; else "$@"; fi; }

# Tout le script est dans une fonction appelée à la fin : bash le lit en entier avant de l'exécuter
# (indispensable avec « curl … | bash », et parce que le dossier ~/comaxians peut être supprimé).
main() {
while [[ $# -gt 0 ]]; do
  case $1 in
    --tout)    PURGE=1; shift ;;
    --serveur) DO_SCREEN=0; shift ;;
    --ecran)   DO_SERVER=0; shift ;;
    --oui)     YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *)         printf 'Option inconnue : %s\n\n' "$1"; usage 1 ;;
  esac
done
(( DO_SERVER + DO_SCREEN > 0 )) || die "--serveur et --ecran sont incompatibles (sans option, les deux rôles sont retirés)"
# En root, l'écran (installé dans le dossier de l'utilisateur) serait ignoré : refusé, sauf --serveur
if [[ $EUID -eq 0 && $DRY_RUN == 0 && $DO_SCREEN == 1 ]]; then
  die "lancez ce script avec l'utilisateur qui ouvre la session graphique (pas avec sudo) : il demandera sudo lui-même (serveur seul installé en root : ajoutez --serveur)"
fi
# Root sur un système sans sudo (serveur seul sur une image minimale)
if [[ $EUID -eq 0 ]] && ! command -v sudo >/dev/null; then sudo() { "$@"; }; fi

# ------------------------------------------------------------------ détection
HAS_SERVER=0
[[ -e $UNIT_FILE || -d $APP_DIR || -e $ENV_FILE || -d $DATA_DIR || -e $BACKUP_BIN ]] && HAS_SERVER=1
HAS_SCREEN=0
if [[ -e $KIOSK || -e $AUTOSTART ]] || grep -qs 'axians-kiosk' "$LABWC"; then HAS_SCREEN=1; fi
HAS_SRC=0; [[ -f $SRC/deploy/install-serveur-et-ecran.sh ]] && HAS_SRC=1

REMOVE_SERVER=$(( DO_SERVER && HAS_SERVER ))
REMOVE_SCREEN=$(( DO_SCREEN && HAS_SCREEN ))
FULL=$(( DO_SERVER && DO_SCREEN ))     # désinstallation complète (pas seulement un rôle)

EXTRAS=()
if [[ $PURGE == 1 && $FULL == 1 ]]; then
  for x in "$HOME"/comaxians.ancien-* "$HOME/comaxians-data" "$HOME/comaxians-pi.tar.gz"; do
    [[ -e $x ]] && EXTRAS+=("$x")
  done
fi
HAS_BACKUPS=0; [[ -d $BACKUP_DIR ]] && HAS_BACKUPS=1

log "Désinstallation Axians – ce qui va être supprimé"
[[ $DRY_RUN == 1 ]] && warn "MODE SIMULATION : rien ne sera modifié"
nothing=1
if [[ $REMOVE_SERVER == 1 ]]; then
  nothing=0
  info "• Rôle SERVEUR : service « comaxians », application ($APP_DIR), configuration et mot de passe,"
  info "  contenus et images ($DATA_DIR), outils et planification des sauvegardes, utilisateur « $SERVICE_USER »"
  [[ $PURGE == 0 ]] && info "  → vos contenus sont d'abord sauvegardés dans $BACKUP_DIR (conservé)"
fi
if [[ $REMOVE_SCREEN == 1 ]]; then
  nothing=0
  info "• Rôle ÉCRAN : affichage automatique au démarrage (kiosque), redémarrage quotidien,"
  info "  résolution HDMI imposée ; la mise en veille de l'écran est réactivée"
fi
if [[ $FULL == 1 && $HAS_SRC == 1 ]]; then nothing=0; info "• Code téléchargé : $SRC"; fi
if [[ $PURGE == 1 && $DO_SERVER == 1 && $HAS_BACKUPS == 1 ]]; then nothing=0; info "• TOUTES les sauvegardes : $BACKUP_DIR"; fi
for x in "${EXTRAS[@]}"; do nothing=0; info "• Copie : $x"; done
if [[ $nothing == 1 ]]; then
  info "Rien à désinstaller sur ce Raspberry Pi."
  exit 0
fi

# ---------------------------------------------------------------- confirmation
if [[ $YES == 0 && $DRY_RUN == 0 ]]; then
  if { true </dev/tty; } 2>/dev/null; then
    echo
    [[ $PURGE == 1 ]] && warn "--tout : vos contenus et leurs sauvegardes seront définitivement perdus."
    read -r -p "    Tapez SUPPRIMER pour confirmer : " answer </dev/tty
    [[ $answer == "SUPPRIMER" ]] || die "désinstallation annulée"
  else
    die "pas de terminal pour confirmer : relancez avec --oui"
  fi
fi

# =========================================================== RÔLE SERVEUR
if [[ $REMOVE_SERVER == 1 ]]; then
  if [[ $PURGE == 0 ]] && [[ -x $BACKUP_BIN ]] && sudo test -f "$DATA_DIR/comaxians.db"; then
    log "Sauvegarde de vos contenus avant suppression"
    # En cas d'échec, la désinstallation s'arrête : rien n'est supprimé
    run sudo env COMAXIANS_BACKUP_LABEL=avant-desinstallation "$BACKUP_BIN"
  fi

  log "Arrêt et suppression du serveur"
  if [[ -e $UNIT_FILE ]]; then
    run sudo systemctl disable --now comaxians || warn "arrêt du service impossible (déjà arrêté ?)"
  fi
  run sudo rm -f "$UNIT_FILE" "$ENV_FILE" "$CRON_BACKUP" "$BACKUP_BIN" "$RESTORE_BIN"
  run sudo systemctl daemon-reload || true
  run sudo rm -rf "$APP_DIR" "$DATA_DIR"
  if id "$SERVICE_USER" &>/dev/null; then
    run sudo userdel "$SERVICE_USER" || warn "suppression de l'utilisateur « $SERVICE_USER » impossible"
  fi
fi
if [[ $PURGE == 1 && $DO_SERVER == 1 && $HAS_BACKUPS == 1 ]]; then
  log "Suppression de toutes les sauvegardes"
  run sudo rm -rf "$BACKUP_DIR"
fi

# ============================================================= RÔLE ÉCRAN
if [[ $REMOVE_SCREEN == 1 ]]; then
  log "Arrêt et suppression de l'affichage"
  run pkill -f 'axians-kiosk\.sh' || true
  run pkill -f -- '--kiosk.*/display' || true
  run rm -f "$KIOSK" "$AUTOSTART"
  if grep -qs 'axians-kiosk' "$LABWC"; then
    run sed -i '/axians-kiosk/d' "$LABWC"
    # fichier créé uniquement pour l'écran et désormais vide : on le retire
    if [[ $DRY_RUN == 0 ]] && ! grep -q '[^[:space:]]' "$LABWC"; then rm -f "$LABWC"; fi
  fi

  info "redémarrage quotidien retiré"
  if [[ $DRY_RUN == 1 ]]; then
    info "[simulation] suppression de la ligne « axians-reboot » de la crontab root"
  else
    ( sudo crontab -l 2>/dev/null | grep -v 'axians-reboot' || true ) | sudo crontab -
  fi

  for f in "${CMDLINES[@]}"; do
    [[ -f $f.avant-axians ]] || continue   # sans copie d'origine, le fichier n'a pas été modifié par nous
    info "résolution HDMI d'origine rétablie ($f)"
    orig=$(grep -o 'video=HDMI-A-1:[^[:space:]]*' "$f.avant-axians" || true)
    if [[ -n $orig ]]; then
      run sudo sed -i -E "s#video=HDMI-A-1:[^[:space:]]+#$orig#" "$f"
    else
      run sudo sed -i -E 's/[[:space:]]*video=HDMI-A-1:[^[:space:]]+//' "$f"
    fi
    run sudo rm -f "$f.avant-axians"
  done

  if command -v raspi-config >/dev/null; then
    info "mise en veille de l'écran réactivée"
    run sudo raspi-config nonint do_blanking 0 || true
  fi
fi

# ======================================================== CODE ET COPIES
if [[ $FULL == 1 && $HAS_SRC == 1 ]]; then
  log "Suppression du code téléchargé ($SRC)"
  run rm -rf "$SRC"
fi
for x in "${EXTRAS[@]}"; do
  run rm -rf "$x"
done

# ------------------------------------------------------------------ résumé
echo
printf '\033[1;32m%s\033[0m\n' "Désinstallation terminée."
if [[ $REMOVE_SERVER == 1 && $PURGE == 0 ]]; then
  cat <<EOF

  Vos contenus sont sauvegardés dans $BACKUP_DIR
  (fichier comaxians-avant-desinstallation-…). Après une réinstallation, retrouvez-les avec :
    sudo comaxians-restore
  Pour supprimer aussi ces sauvegardes : relancez ce script avec --tout
EOF
fi
cat <<EOF

  Laissés en place volontairement (utiles à d'autres usages du Pi) :
   - les logiciels installés (Chromium, police Inter, Python, curl, rsync, Git) ;
     pour les retirer : sudo apt remove fonts-inter   (par exemple)
   - la taille de la mémoire d'échange (swap) et le profil du navigateur.

  Redémarrez pour terminer :  sudo reboot

EOF
}

main "$@" </dev/null

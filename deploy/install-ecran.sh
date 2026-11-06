#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : installation d'un ÉCRAN (rôle « affichage » seul)
#
# Transforme un Raspberry Pi (Raspberry Pi OS Desktop, Bookworm ou plus récent) en écran
# qui affiche, en plein écran et au démarrage, les contenus d'un serveur Axians existant.
# Rien d'autre n'est nécessaire sur ce Pi : pas de copie de l'application.
#
# Utilisation (avec votre utilisateur habituel, pas en root) :
#   bash install-ecran.sh --server 192.168.1.20 --screen accueil
# ou directement depuis le serveur, sans rien copier :
#   curl -fsSL http://192.168.1.20:8080/install-ecran.sh | bash -s -- --server 192.168.1.20 --screen accueil
# =============================================================================
set -euo pipefail

SERVER=""
URL=""
SCREEN_NAME=""
LITE=auto
RESOLUTION=auto
DRY_RUN=0
KIOSK_SCRIPT="$HOME/axians-kiosk.sh"

usage() {
  cat <<'EOF'
Installation d'un écran Axians (Raspberry Pi en mode kiosque)

  bash install-ecran.sh --server ADRESSE[:PORT] [options]

Options :
  --server ADRESSE[:PORT]  adresse du serveur Axians (port 8080 par défaut)
  --screen NOM             nom de l'écran : ciblage des contenus et thème réglable dans l'admin
  --full-animations        garder toutes les animations (par défaut : allégées sur Pi 3)
  --resolution R           720p | 1080p | keep (par défaut : 720p sur Pi 3, inchangée sinon)
  --url URL                adresse complète de la page d'affichage (usage avancé, remplace --server)
  --dry-run                simulation : affiche ce qui serait fait, sans rien modifier
  -h, --help               cette aide

Relancer le script met à jour la configuration de l'écran.
EOF
  exit "${1:-0}"
}

# ----------------------------------------------------------------- utilitaires
log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[1;33m    ATTENTION : %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }
run() {
  if [[ $DRY_RUN == 1 ]]; then printf '    [simulation] %s\n' "$*"; else "$@"; fi
}
# Écrit un fichier de l'utilisateur (contenu sur l'entrée standard)
write_user_file() {   # write_user_file CHEMIN MODE
  if [[ $DRY_RUN == 1 ]]; then
    printf '    [simulation] écriture de %s :\n' "$1"; sed 's/^/        | /'
  else
    mkdir -p "$(dirname "$1")"; cat >"$1"; chmod "$2" "$1"
  fi
}

# Tout le script est dans une fonction appelée à la toute fin : bash le lit donc en entier avant
# de l'exécuter (indispensable avec « curl … | bash », où les commandes partagent l'entrée standard).
main() {
while [[ $# -gt 0 ]]; do
  case $1 in
    --server)          SERVER=${2:?adresse manquante}; shift 2 ;;
    --url)             URL=${2:?adresse manquante}; shift 2 ;;
    --screen)          SCREEN_NAME=${2:?nom manquant}; shift 2 ;;
    --full-animations) LITE=0; shift ;;
    --resolution)      RESOLUTION=${2:?valeur manquante}; shift 2 ;;
    --dry-run)         DRY_RUN=1; shift ;;
    -h|--help)         usage 0 ;;
    *)                 printf 'Option inconnue : %s\n\n' "$1"; usage 1 ;;
  esac
done

# ---------------------------------------------------------------- vérifications
[[ -n $SERVER || -n $URL ]] || { printf 'Indiquez l’adresse du serveur avec --server.\n\n'; usage 1; }
case $RESOLUTION in auto|720p|1080p|keep) ;; *) die "--resolution doit valoir 720p, 1080p ou keep" ;; esac
if [[ -n $SCREEN_NAME && ! $SCREEN_NAME =~ ^[A-Za-z0-9_-]{1,40}$ ]]; then
  die "nom d'écran invalide (lettres, chiffres, - et _ uniquement, 40 caractères max)"
fi
if [[ $EUID -eq 0 && $DRY_RUN == 0 ]]; then
  die "lancez ce script avec l'utilisateur qui ouvre la session graphique (pas avec sudo) : il demandera sudo lui-même"
fi

MODEL=$(tr -d '\0' 2>/dev/null </proc/device-tree/model || echo "modèle inconnu")
IS_PI3=0; [[ $MODEL == *"Raspberry Pi 3"* ]] && IS_PI3=1
[[ $LITE == auto ]] && LITE=$IS_PI3
if [[ $RESOLUTION == auto ]]; then
  if [[ $IS_PI3 == 1 ]]; then RESOLUTION=720p; else RESOLUTION=keep; fi
fi

if [[ -z $URL ]]; then
  [[ $SERVER =~ ^[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] || die "adresse de serveur invalide : $SERVER (exemple : 192.168.1.20 ou 192.168.1.20:8080)"
  [[ $SERVER == *:* ]] || SERVER="$SERVER:8080"
  URL="http://$SERVER/display"
  QUERY=()
  [[ -n $SCREEN_NAME ]] && QUERY+=("screen=$SCREEN_NAME")
  [[ $LITE == 1 ]] && QUERY+=("lite=1")
  if (( ${#QUERY[@]} )); then URL+="?$(IFS='&'; echo "${QUERY[*]}")"; fi
fi
BASE_URL=${URL%%\?*}          # http://serveur:8080/display
API_URL=${BASE_URL%/display}/api/playlist

log "Installation d'un écran Axians – $MODEL"
info "Page affichée : $URL"
info "Animations : $([[ $LITE == 1 ]] && echo 'allégées (lite)' || echo 'complètes')  ·  Sortie HDMI : $RESOLUTION"
[[ $DRY_RUN == 1 ]] && warn "MODE SIMULATION : rien ne sera modifié"

if command -v curl >/dev/null && curl -fs -m 5 -o /dev/null "$API_URL"; then
  info "serveur joignable ✔"
else
  warn "le serveur ne répond pas pour l'instant ($API_URL). L'installation continue ; l'écran attendra qu'il réponde."
fi

# -------------------------------------------------------------------- paquets
log "Navigateur Chromium, police Inter"
run sudo apt-get update -y
if [[ $DRY_RUN == 1 ]] || apt-cache show chromium >/dev/null 2>&1; then BROWSER=chromium
else BROWSER=chromium-browser; fi
run sudo apt-get install -y "$BROWSER" curl
run sudo apt-get install -y fonts-inter || warn "police Inter indisponible : police de repli utilisée"

log "Écran toujours allumé (mise en veille désactivée)"
run sudo raspi-config nonint do_blanking 1 || warn "raspi-config indisponible : désactivez la mise en veille manuellement"

# --------------------------------------------------------------- kiosque
log "Script de lancement : $KIOSK_SCRIPT"
{
  printf '#!/usr/bin/env bash\n# Lancement de l’écran Axians (généré par install-ecran.sh – relancez ce dernier pour modifier)\n'
  printf 'URL=%q\nBROWSER=%q\n' "$URL" "$BROWSER"
  cat <<'EOF'
# Une seule instance, même si plusieurs mécanismes de démarrage automatique la lancent
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/axians-kiosk.lock"
flock -n 9 || exit 0
# Attend que le serveur réponde (démarrage du réseau, du Wi-Fi ou du serveur local) – 10 min max
for _ in $(seq 1 120); do
  curl -fs -m 3 -o /dev/null "${URL%%\?*}" && break
  sleep 5
done
PREFS="$HOME/.config/chromium/Default/Preferences"
# Relance automatique du navigateur en cas de plantage
while true; do
  # Évite la bulle « Chromium ne s'est pas fermé correctement » après une coupure
  [ -f "$PREFS" ] && sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
  "$BROWSER" --kiosk --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --disable-features=Translate,TranslateUI --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required --password-store=basic \
    --overscroll-history-navigation=0 "$URL"
  sleep 5
done
EOF
} | write_user_file "$KIOSK_SCRIPT" 755

log "Démarrage automatique à l'ouverture de session"
write_user_file "$HOME/.config/autostart/axians-kiosk.desktop" 644 <<EOF
[Desktop Entry]
Type=Application
Name=Axians – écran
Exec=$KIOSK_SCRIPT
X-GNOME-Autostart-enabled=true
EOF
# Session Wayland « labwc » (Raspberry Pi OS récent) : ajout sans écraser l'existant.
# Le verrou du script empêche un double lancement si les deux mécanismes s'exécutent.
LABWC=$HOME/.config/labwc/autostart
if grep -qs 'axians-kiosk' "$LABWC"; then
  info "labwc : déjà configuré"
elif [[ $DRY_RUN == 1 ]]; then
  info "[simulation] ajout de « $KIOSK_SCRIPT & » à $LABWC"
else
  mkdir -p "$(dirname "$LABWC")"; echo "$KIOSK_SCRIPT &" >>"$LABWC"
fi

# -------------------------------------------------------------- mémoire (Pi 3)
if [[ $IS_PI3 == 1 && -f /etc/dphys-swapfile ]]; then
  current=$(grep -E '^CONF_SWAPSIZE=' /etc/dphys-swapfile | cut -d= -f2 || true)
  if [[ -z $current || $current -lt 512 ]]; then
    log "Mémoire d'échange portée à 512 Mo (évite les plantages de Chromium sur 1 Go)"
    run sudo sed -i -E 's/^#?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
    run sudo dphys-swapfile swapoff
    run sudo dphys-swapfile setup
    run sudo dphys-swapfile swapon
  fi
fi

# ------------------------------------------------------------ résolution HDMI
if [[ $RESOLUTION != keep ]]; then
  CMDLINE=/boot/firmware/cmdline.txt; [[ -f $CMDLINE ]] || CMDLINE=/boot/cmdline.txt
  if [[ -f $CMDLINE || $DRY_RUN == 1 ]]; then
    MODE=1280x720@60; [[ $RESOLUTION == 1080p ]] && MODE=1920x1080@60
    log "Sortie HDMI forcée en ${MODE%@*} ($CMDLINE, effective au prochain démarrage)"
    [[ -f $CMDLINE.avant-axians ]] || run sudo cp "$CMDLINE" "$CMDLINE.avant-axians"
    if [[ -f $CMDLINE ]] && grep -q 'video=HDMI-A-1:' "$CMDLINE"; then
      run sudo sed -i -E "s/video=HDMI-A-1:[^ ]+/video=HDMI-A-1:$MODE/" "$CMDLINE"
    else
      # cmdline.txt doit rester sur une seule ligne : on complète la première
      run sudo sed -i -E "1 s/[[:space:]]*\$/ video=HDMI-A-1:$MODE/" "$CMDLINE"
    fi
  else
    warn "cmdline.txt introuvable : résolution non modifiée"
  fi
fi

# -------------------------------------------------------- redémarrage quotidien
log "Redémarrage quotidien à 4 h 30 (stabilité sur la durée)"
if [[ $DRY_RUN == 1 ]]; then
  info "[simulation] crontab root : 30 4 * * * /sbin/shutdown -r now"
else
  ( sudo crontab -l 2>/dev/null | grep -v 'axians-reboot' || true
    echo "30 4 * * * /sbin/shutdown -r now # axians-reboot" ) | sudo crontab -
fi

# ----------------------------------------------------------------- résumé
cat <<EOF

$(printf '\033[1;32m')Écran configuré.$(printf '\033[0m')

  Page affichée : $URL

  À faire maintenant :
   1. Si ce n'est pas déjà fait, activez la connexion automatique au bureau :
        sudo raspi-config  → System Options → Auto Login → Desktop
   2. Redémarrez :  sudo reboot
$( [[ -n $SCREEN_NAME ]] && printf '   3. L’écran « %s » apparaîtra dans l’admin (section Écrans) : choisissez-y son thème.\n' "$SCREEN_NAME" )

  Pour retirer l'affichage de ce Pi plus tard :
    curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/deploy/desinstaller.sh | bash

EOF
}

main "$@" </dev/null

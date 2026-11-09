#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : installation (ou mise à jour) d'un Raspberry Pi
# directement depuis GitHub, en une seule commande.
#
# Serveur + écran sur le même Pi (tout-en-un) :
#   curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --screen accueil
# Serveur seul (sans écran sur ce Pi) :
#   curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --serveur-seul
#
# Le code est téléchargé (ou mis à jour) dans ~/comaxians, puis deploy/install-serveur-et-ecran.sh
# (ou deploy/install-serveur.sh avec --serveur-seul) est lancé avec les autres options données
# (voir : bash ~/comaxians/deploy/install-serveur-et-ecran.sh --help).
# Relancer la commande met à jour l'application ; vos contenus sont conservés. Sur un Pi installé
# en « serveur seul », la mise à jour reste en « serveur seul » même sans l'option.
# =============================================================================
set -euo pipefail

REPO=${COMAXIANS_REPO:-https://github.com/garg24/comaxians.git}
BRANCH=${COMAXIANS_BRANCH:-main}
DEST=${COMAXIANS_SRC:-$HOME/comaxians}
P=${AXIANS_TEST_PREFIX:-}     # uniquement pour les tests automatisés
UNIT_FILE=$P/etc/systemd/system/comaxians.service
KIOSK=$HOME/axians-kiosk.sh

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }

# Tout le script est dans une fonction appelée à la fin : bash le lit donc en entier avant de
# l'exécuter (indispensable avec « curl … | bash »).
main() {
  local mode="" args=()
  for a in "$@"; do
    case $a in
      --serveur-seul) mode=serveur ;;
      --tout-en-un)   mode=tout ;;
      *)              args+=("$a") ;;
    esac
  done

  if [[ $EUID -eq 0 && $mode != serveur ]]; then
    die "lancez cette commande avec votre utilisateur habituel (pas avec sudo)"
  fi
  # Root sur un système sans sudo (serveur seul sur une image minimale)
  if [[ $EUID -eq 0 ]] && ! command -v sudo >/dev/null; then sudo() { "$@"; }; fi

  if ! command -v git >/dev/null; then
    log "Installation de Git"
    sudo apt-get update -y
    sudo apt-get install -y git
  fi

  if [[ -d $DEST/.git ]]; then
    log "Mise à jour du code dans $DEST"
    git -C "$DEST" pull --ff-only origin "$BRANCH" \
      || die "impossible de mettre à jour $DEST (fichiers modifiés localement ?). Renommez ce dossier puis relancez la commande."
  else
    if [[ -e $DEST ]]; then
      # Dossier d'une installation précédente par archive : on le met de côté (rien n'est supprimé)
      old="$DEST.ancien-$(date +%Y%m%d-%H%M%S)"
      log "$DEST existe déjà (installation par archive) : renommé en $old"
      mv "$DEST" "$old"
    fi
    log "Téléchargement du code depuis $REPO"
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$DEST"
  fi

  # Sans option : un Pi déjà installé en « serveur seul » (service présent, pas d'écran) le reste
  if [[ -z $mode && -f $UNIT_FILE && ! -f $KIOSK ]]; then
    mode=serveur
    log "Ce Pi est installé en « serveur seul » : mise à jour du serveur uniquement"
    echo "    (pour ajouter aussi l'écran sur ce Pi : relancez avec --tout-en-un)"
  fi
  local script=install-serveur-et-ecran.sh
  [[ $mode == serveur ]] && script=install-serveur.sh

  log "Lancement de l'installation ($script)"
  # Le script d'installation peut poser des questions (mot de passe admin) :
  # on lui donne le clavier (/dev/tty) plutôt que le flux du téléchargement.
  if { true </dev/tty; } 2>/dev/null; then
    exec bash "$DEST/deploy/$script" "${args[@]}" </dev/tty
  else
    exec bash "$DEST/deploy/$script" "${args[@]}"
  fi
}

main "$@" </dev/null

#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : installation (ou mise à jour) du Raspberry Pi
# « serveur + écran » directement depuis GitHub, en une seule commande :
#
#   curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --screen accueil
#
# Le code est téléchargé (ou mis à jour) dans ~/comaxians, puis
# deploy/install-serveur-et-ecran.sh est lancé avec les options données
# (voir : bash ~/comaxians/deploy/install-serveur-et-ecran.sh --help).
# Relancer la même commande met à jour l'application ; vos contenus sont conservés.
# =============================================================================
set -euo pipefail

REPO=${COMAXIANS_REPO:-https://github.com/garg24/comaxians.git}
BRANCH=${COMAXIANS_BRANCH:-main}
DEST=${COMAXIANS_SRC:-$HOME/comaxians}

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }

# Tout le script est dans une fonction appelée à la fin : bash le lit donc en entier avant de
# l'exécuter (indispensable avec « curl … | bash »).
main() {
  [[ $EUID -ne 0 ]] || die "lancez cette commande avec votre utilisateur habituel (pas avec sudo)"

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

  log "Lancement de l'installation"
  # Le script d'installation peut poser des questions (mot de passe admin) :
  # on lui donne le clavier (/dev/tty) plutôt que le flux du téléchargement.
  if { true </dev/tty; } 2>/dev/null; then
    exec bash "$DEST/deploy/install-serveur-et-ecran.sh" "$@" </dev/tty
  else
    exec bash "$DEST/deploy/install-serveur-et-ecran.sh" "$@"
  fi
}

main "$@" </dev/null

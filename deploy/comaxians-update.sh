#!/usr/bin/env bash
# =============================================================================
# Axians – affichage dynamique : mise à jour du serveur demandée depuis l'admin
#
# Installé par install-serveur.sh dans /usr/local/bin/comaxians-update et lancé par systemd
# (comaxians-update.service, en root) quand l'admin dépose le fichier de demande
# /var/lib/comaxians/update-request (surveillé par comaxians-update.path).
# Le serveur web n'a ainsi aucun droit d'administration : il ne fait que déposer une demande.
#
# Étapes : récupération du code depuis GitHub (git pull, avec l'utilisateur propriétaire du
# dossier source), puis install-serveur.sh (sauvegarde avant mise à jour, contenus et mot de
# passe conservés, redémarrage du service). L'avancement est écrit dans update-status.json.
# =============================================================================
set -uo pipefail

CONF=${COMAXIANS_UPDATE_CONF:-/etc/comaxians-update.conf}
DATA_DIR=${COMAXIANS_DATA_DIR:-/var/lib/comaxians}
REQUEST=$DATA_DIR/update-request
STATUS=$DATA_DIR/update-status.json
LOG=$DATA_DIR/update.log
BRANCH=main

status() {   # status ETAT MESSAGE
  local msg=${2//\\/\\\\}; msg=${msg//\"/\\\"}
  printf '{"state": "%s", "message": "%s", "time": "%s"}\n' "$1" "$msg" "$(date +%Y-%m-%dT%H:%M:%S)" >"$STATUS.tmp"
  chmod 644 "$STATUS.tmp"; mv -f "$STATUS.tmp" "$STATUS"
}
fail() { echo "ÉCHEC : $*" >>"$LOG"; status error "$*"; exit 1; }

as_owner() {   # commande lancée avec l'utilisateur propriétaire du dossier source
  if [[ -n ${SRC_USER:-} && $SRC_USER != root && $(id -u) == 0 ]]; then
    runuser -u "$SRC_USER" -- env HOME="$(getent passwd "$SRC_USER" | cut -d: -f6)" "$@"
  else
    "$@"
  fi
}

# Tout est dans une fonction : bash lit le script en entier avant de l'exécuter
# (install-serveur.sh remplace ce fichier pendant la mise à jour).
main() {
rm -f "$REQUEST"
: >"$LOG"; chmod 644 "$LOG"
exec >>"$LOG" 2>&1
echo "== Mise à jour demandée depuis l'admin – $(date)"

# shellcheck source=/dev/null
[[ -f $CONF ]] && source "$CONF"
SRC_DIR=${SRC_DIR:-}
SRC_USER=${SRC_USER:-}
[[ -n $SRC_DIR && -d $SRC_DIR/.git ]] \
  || fail "le code n'a pas été installé depuis GitHub (installation par archive) : relancez la commande d'installation sur le Raspberry Pi"

status running "Téléchargement de la nouvelle version…"
as_owner git -C "$SRC_DIR" pull --ff-only origin "$BRANCH" \
  || fail "téléchargement impossible (pas d'accès à GitHub, ou fichiers modifiés dans $SRC_DIR) – détails dans $LOG"

status running "Installation (sauvegarde, copie, redémarrage du serveur)…"
bash "$SRC_DIR/deploy/install-serveur.sh" --no-summary \
  || fail "l'installation a échoué – vos contenus sont intacts (sauvegarde avant-maj faite) ; détails dans $LOG"

status done "Mise à jour terminée : version $(as_owner git -C "$SRC_DIR" log -1 --format='%h – %s')"
echo "== Terminé – $(date)"
}

main "$@"

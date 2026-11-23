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
# passe conservés, redémarrage du service). L'avancement est écrit dans update-status.json
# (étape en cours, pourcentage) et affiché par l'admin, avec la fin du journal update.log.
# =============================================================================
set -uo pipefail
shopt -s lastpipe   # la boucle qui suit l'installation garde ses variables (étape en cours)

CONF=${COMAXIANS_UPDATE_CONF:-/etc/comaxians-update.conf}
DATA_DIR=${COMAXIANS_DATA_DIR:-/var/lib/comaxians}
REQUEST=$DATA_DIR/update-request
STATUS=$DATA_DIR/update-status.json
LOG=$DATA_DIR/update.log
BRANCH=main

# Étapes affichées dans l'admin, et pourcentage atteint au début de chacune
# (proportionnel à la durée habituelle sur un Raspberry Pi : les paquets système sont le plus long)
STEPS=("Téléchargement de la nouvelle version"
       "Sauvegarde de vos contenus"
       "Mise à jour des paquets système"
       "Copie des fichiers de l'application"
       "Mise à jour des composants Python"
       "Redémarrage du serveur"
       "Finalisation")
PCT=(0 8 18 55 62 80 94 100)
STEP=0
STARTED=$(date +%Y-%m-%dT%H:%M:%S)

json_str() { local s=${1//\\/\\\\}; s=${s//\"/\\\"}; printf '"%s"' "$s"; }

status() {   # status ETAT MESSAGE   (étape en cours : $STEP, de 1 à ${#STEPS[@]})
  local steps="" l from=0 to=0
  for l in "${STEPS[@]}"; do steps+="${steps:+, }$(json_str "$l")"; done
  if [[ $1 == done ]]; then from=100 to=100
  elif (( STEP > 0 )); then from=${PCT[STEP-1]} to=${PCT[STEP]}; fi
  printf '{"state": "%s", "message": %s, "time": "%s", "started": "%s", "step": %d, "steps": [%s], "percent": %d, "next_percent": %d}\n' \
    "$1" "$(json_str "$2")" "$(date +%Y-%m-%dT%H:%M:%S)" "$STARTED" "$STEP" "$steps" "$from" "$to" >"$STATUS.tmp"
  chmod 644 "$STATUS.tmp"; mv -f "$STATUS.tmp" "$STATUS"
}
step() {   # step N : passe à l'étape N (jamais en arrière)
  (( $1 > STEP )) || return 0
  STEP=$1
  status running "${STEPS[STEP-1]}…"
}
fail() { echo "ÉCHEC : $*" >>"$LOG"; status error "$*"; exit 1; }

# Recopie la sortie d'install-serveur.sh dans le journal et reconnaît ses étapes (lignes « ==> »)
follow_install() {
  local line
  while IFS= read -r line; do
    printf '%s\n' "$line"
    case $line in
      *'==> [Serveur] Outils de sauvegarde'*|*'==> [Serveur] Sauvegarde'*) step 2 ;;
      *'==> [Serveur] Paquets système'*) step 3 ;;
      *'==> [Serveur] Utilisateur de service'*|*'==> [Serveur] Copie'*) step 4 ;;
      *'==> [Serveur] Environnement Python'*) step 5 ;;
      *'==> [Serveur] Données'*|*'==> [Serveur] Configuration'*|*'==> [Serveur] Service systemd'*) step 6 ;;
      *'==> [Serveur] Bouton'*) step 7 ;;
    esac
  done
}

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

step 1
as_owner git -C "$SRC_DIR" pull --ff-only origin "$BRANCH" \
  || fail "téléchargement impossible (pas d'accès à GitHub, ou fichiers modifiés dans $SRC_DIR) – détails dans $LOG"

bash "$SRC_DIR/deploy/install-serveur.sh" --no-summary 2>&1 | follow_install \
  || fail "l'installation a échoué – vos contenus sont intacts (sauvegarde avant-maj faite) ; détails dans $LOG"

STEP=${#STEPS[@]}
status done "Mise à jour terminée : version $(as_owner git -C "$SRC_DIR" log -1 --format='%h – %s')"
echo "== Terminé – $(date)"
}

main "$@"

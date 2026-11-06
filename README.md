# Axians – Affichage dynamique

Application de communication interne qui diffuse des contenus (succès, actualités, partenaires, événements, chiffres clés…) sur des écrans pilotés par des Raspberry Pi, avec un design sombre et moderne autour du logo Axians.

```
┌──────────────────────────┐        ┌───────────────────────────┐
│  Serveur (cette appli)   │◄──────►│ Raspberry Pi + écran       │  Chromium en mode kiosque
│  /admin   gestion        │  HTTP  │ http://serveur:8080/display│  (interroge le serveur
│  /display player         │        └───────────────────────────┘   toutes les 60 s)
│  /api/playlist           │◄──────►  autres écrans… (?screen=accueil, ?screen=cafeteria)
└──────────────────────────┘
```

**Installation express sur un Raspberry Pi** (serveur + écran, Raspberry Pi OS Desktop avec accès Internet) :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --screen accueil
```

Détails, écrans supplémentaires et mises à jour : voir [Mise en production](#mise-en-production).

## Fonctionnalités

- **10 modèles de diapositives** : Succès, Actualité, Partenaire, Événement, Employé du mois, Anniversaires, Chiffre clé, Message, Vidéo, Affiche plein écran.
- **Anniversaires** : une liste Prénom / Nom / date (JJ/MM, saisie ligne par ligne ou import par copier-coller depuis Excel). La diapositive ne s'affiche automatiquement que les jours où quelqu'un est fêté (ballons, confettis, cartes prénom-nom). Seules les personnes fêtées aujourd'hui ou demain sont envoyées aux écrans ; l'année de naissance n'est jamais conservée.
- **Employé du mois** : portrait rond avec anneau doré (initiales si pas de photo), nom, poste/équipe, période en badge et quelques mots affichés comme une citation.
- **Vidéo** :
  - diapositive *Vidéo* plein écran, lue jusqu'au bout (ou pendant une durée imposée, en boucle), avec titre facultatif affiché quelques secondes en bas d'écran et son activable ;
  - une courte vidéo peut aussi remplacer la photo d'une diapositive Succès, Actualité ou Événement (lecture en boucle, sans son).
- **Design** : thème sombre, halos de couleur animés propres à chaque type de contenu, typographie Inter très grasse, cartes translucides, indicateur de progression segmenté. Seul le logo Axians (version blanche, extraite en vectoriel de la charte) est conservé de l'identité officielle.
- **Entités** (Cloud, Communication…) : chaque diapositive peut être rattachée à une ou plusieurs entités, affichées en étiquettes colorées à côté du surtitre. La liste des entités se modifie dans les paramètres de l'admin.
- **Compteur de jours automatique** (diapositive Chiffre clé, ex. « jours sans accident ») : on saisit la date du dernier événement, l'écran calcule et incrémente le nombre chaque jour, même hors ligne. Bouton « Remettre à zéro » dans la liste des contenus en cas de nouvel événement.
- **Disposition au choix** pour les diapositives Succès, Actualité, Partenaire et Événement : texte à gauche et visuel à droite, ou l'inverse (champ « Disposition » du formulaire).
- **Programmation** : dates de début et de fin, durée par diapositive, activation/désactivation, ordre de passage.
- **Ciblage des écrans** : un même serveur pour plusieurs écrans (`/display?screen=accueil`).
- **Thème clair ou sombre, écran par écran** : section « Écrans » de l'admin. Chaque écran y apparaît automatiquement à sa première connexion, avec son état (en ligne / dernière connexion). On choisit pour chacun *Sombre*, *Clair*, *Alterné* (une diapositive sombre, une claire, avec fondu enchaîné) ou *Par défaut* (thème par défaut réglable dans les paramètres). Le changement s'applique en moins d'une minute, sans toucher au Raspberry Pi.
- **Bandeau défilant « En bref »**, heure et date, barre de progression.
- **Robuste** : la playlist est mise en cache sur l'écran (continue de tourner si le réseau tombe), les modifications s'appliquent sans recharger la page, le texte trop long est réduit automatiquement, rechargement quotidien.
- **Aperçu** de chaque diapositive depuis l'admin ; flèche droite / espace pour passer à la suivante.

## Démarrage rapide (poste de test)

```bash
git clone https://github.com/garg24/comaxians.git
cd comaxians
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
.venv\Scripts\python run.py
```

- Administration : http://localhost:8080/admin (mot de passe par défaut : `axians`)
- Écran : http://localhost:8080/display

Variables d'environnement :

| Variable | Rôle | Défaut |
|---|---|---|
| `COMAXIANS_ADMIN_PASSWORD` | mot de passe de l'admin | `axians` (**à changer**) |
| `COMAXIANS_SECRET_KEY` | clé de session | générée dans `data/.secret_key` |
| `COMAXIANS_DATA_DIR` | base SQLite + images téléversées | `./data` |
| `PORT` | port HTTP | `8080` |
| `COMAXIANS_MAX_UPLOAD_MB` | taille maximale d'un fichier téléversé | `500` |

## Mise en production

Deux scripts, selon le rôle du Raspberry Pi :

| Script | Rôle du Raspberry Pi | Installation |
|---|---|---|
| `deploy/install-serveur-et-ecran.sh` | **serveur + écran** (un seul Pi de ce type) | une commande, depuis GitHub (ou par archive sans Internet) |
| `deploy/install-ecran.sh` | **écran seul**, relié au serveur | une commande, depuis le serveur ou depuis GitHub |

Les deux scripts adaptent automatiquement les réglages au **Raspberry Pi 3 B** (animations allégées `?lite=1`, sortie HDMI 720p, mémoire d'échange 512 Mo) et gardent les réglages normaux sur Pi 4 / Pi 5. Ils acceptent `--dry-run` (simulation sans rien modifier) et `--help`.

Prérequis sur chaque Pi : Raspberry Pi OS **Desktop** (Bookworm ou plus récent) installé avec Raspberry Pi Imager, SSH activé, connexion automatique au bureau (`sudo raspi-config` → System Options → Auto Login → Desktop).

### Option A – Le Raspberry Pi « serveur + écran »

#### Méthode 1 – Directement depuis GitHub (recommandée)

Sur le Raspberry Pi (terminal ou `ssh`), une seule commande télécharge l'application dans `~/comaxians` et l'installe :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --screen accueil
```

Puis redémarrez :

```bash
sudo reboot
```

Le script demande le mot de passe de l'administration ; toutes les options de `install-serveur-et-ecran.sh` peuvent être ajoutées après `--` (voir plus bas). Sans connexion Internet sur le Pi, utilisez la méthode 2.

**Reprendre vos contenus actuels** (facultatif, à la première installation) – ils ne sont volontairement pas sur GitHub (données personnelles). Depuis le dossier du projet sur votre PC :

```powershell
scp -r data pi@IP-DU-PI:~/comaxians-data
```

puis, sur le Pi, ajoutez `--import-data ~/comaxians-data` à la commande d'installation :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --screen accueil --import-data ~/comaxians-data
```

Sans cette étape, le Pi démarre avec 8 contenus d'exemple (personnes fictives) qui montrent chaque type de diapositive : Succès, Chiffre clé avec compteur de jours automatique, Actualité, Employé du mois, Partenaire, Événement (dans 3 semaines), Anniversaires (une personne fêtée le jour de l'installation) et Message. Modifiez-les ou supprimez-les depuis l'admin.

**Mise à jour** : relancez simplement la commande (le code est mis à jour par `git pull`, vos contenus sont conservés) :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash
```

Variante sans `curl | bash`, pour lire le script avant de l'exécuter :

```bash
git clone https://github.com/garg24/comaxians.git ~/comaxians
bash ~/comaxians/deploy/install-serveur-et-ecran.sh --screen accueil
```

#### Méthode 2 – Par une archive préparée sur le PC (Pi sans Internet)

**1. Sur votre PC Windows** – préparer l'archive (avec `-WithData` pour emporter vos contenus actuels) :

```powershell
powershell -ExecutionPolicy Bypass -File deploy\prepare-pi-package.ps1 -WithData
scp dist\comaxians-pi.tar.gz pi@IP-DU-PI:~/
```

**2. Sur le Raspberry Pi** (via `ssh pi@IP-DU-PI`) :

```bash
mkdir -p ~/comaxians && tar -xzf ~/comaxians-pi.tar.gz -C ~/comaxians
bash ~/comaxians/deploy/install-serveur-et-ecran.sh --screen accueil
sudo reboot
```

**Mise à jour** : refaites l'archive, copiez-la, extrayez-la et relancez le même script.

#### Ce que fait le script d'installation

Le script demande le mot de passe de l'administration, puis :
- installe le serveur dans `/opt/comaxians` comme **service systemd** démarré au boot (utilisateur dédié, données dans `/var/lib/comaxians`, mot de passe dans `/etc/comaxians.env` lisible par root seulement) ;
- reprend vos contenus (`--import-data DOSSIER`, ou dossier `data/` de l'archive) – première installation uniquement ;
- configure l'écran de ce Pi en **kiosque** sur `http://localhost:8080/display` (en appelant `install-ecran.sh`) ;
- publie le script écran à l'adresse `http://IP-DU-PI:8080/install-ecran.sh` pour équiper les autres Pi ;
- programme une **sauvegarde quotidienne** à 3 h 30 dans `/var/backups/comaxians` (14 jours conservés) et installe les commandes `comaxians-backup` et `comaxians-restore` ;
- affiche l'adresse de l'admin à ouvrir depuis votre PC : `http://IP-DU-PI:8080/admin`.

Options : `--screen NOM`, `--full-animations`, `--resolution 720p|1080p|keep`, `--import-data DOSSIER`, `--no-import`, `--dry-run` (simulation sans rien modifier), `--help`.

#### Ce qui est conservé à chaque mise à jour

| Élément | À la mise à jour |
|---|---|
| Application (`/opt/comaxians`) | remplacée par la nouvelle version |
| Contenus, images, écrans, paramètres (`/var/lib/comaxians`) | **conservés** – le dossier `data/` de l'archive est ignoré, même créée avec `-WithData` |
| Mot de passe admin | conservé (sauf si `COMAXIANS_ADMIN_PASSWORD=…` est fourni) |

Juste avant de remplacer l'application, le script fait une **sauvegarde automatique** (`comaxians-avant-maj-….tar.gz`, conservée 90 jours). Si la sauvegarde échoue, la mise à jour s'arrête sans rien modifier.

**Revenir en arrière** (après une erreur de manipulation ou une mise à jour) :

```bash
sudo comaxians-restore                                   # liste les sauvegardes disponibles
sudo comaxians-restore /var/backups/comaxians/comaxians-avant-maj-20261106-103000.tar.gz
```

L'état actuel est lui-même sauvegardé (`comaxians-avant-restauration-…`) avant la restauration ; le serveur est arrêté puis relancé automatiquement.

> Les contenus d'exemple ne sont créés qu'à la toute première installation : si vous supprimez toutes les diapositives, la liste reste vide après un redémarrage ou une mise à jour.

> Pensez à réserver l'adresse IP du Pi dans votre box/routeur, et à copier de temps en temps les sauvegardes hors du Pi (`scp pi@IP-DU-PI:/var/backups/comaxians/*.tar.gz .`) : une carte SD peut lâcher.

### Les autres Raspberry Pi « écran seul »

Rien à copier : sur le nouveau Pi, ouvrez un terminal (ou `ssh`) et lancez la commande affichée dans l'admin, **section Écrans → « Installer un nouvel écran »** (bouton Copier), par exemple :

```bash
curl -fsSL http://IP-DU-SERVEUR:8080/install-ecran.sh | bash -s -- --server IP-DU-SERVEUR --screen hall
sudo reboot
```

Le même script peut aussi être pris directement sur GitHub (utile si le serveur n'est pas encore démarré) :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/deploy/install-ecran.sh | bash -s -- --server IP-DU-SERVEUR --screen hall
```

Le script installe Chromium et la police Inter, désactive la mise en veille, lance l'affichage en plein écran au démarrage (relance automatique en cas de plantage, une seule instance garantie) et programme un redémarrage quotidien à 4 h 30. Après le redémarrage, l'écran apparaît dans l'admin, où l'on choisit son thème. Pour modifier un écran (nom, résolution…), relancez la commande avec les nouvelles options.

Options : `--server ADRESSE[:PORT]` (obligatoire), `--screen NOM`, `--full-animations`, `--resolution 720p|1080p|keep`, `--url URL` (adresse complète, usage avancé), `--dry-run`, `--help`.

### Option B – Serveur sur une autre machine (VM, NAS…)

#### 1. Le serveur

Sur une VM Linux, un NAS ou un Raspberry Pi dédié :

```bash
sudo useradd -r -m -d /opt/comaxians comaxians
sudo -u comaxians git clone https://github.com/garg24/comaxians.git /opt/comaxians
cd /opt/comaxians && sudo -u comaxians python3 -m venv .venv && sudo -u comaxians .venv/bin/pip install -r requirements.txt
sudo cp deploy/comaxians.service /etc/systemd/system/   # éditez le mot de passe
sudo systemctl daemon-reload && sudo systemctl enable --now comaxians
```

Ou avec Docker :

```bash
docker build -t axians-affichage .
docker run -d --restart=always -p 8080:8080 -v axians-data:/data -e COMAXIANS_ADMIN_PASSWORD=... axians-affichage
```

> Pour un accès hors du réseau local, placez le serveur derrière un reverse proxy HTTPS (nginx, Traefik) ou l'authentification de l'entreprise.

#### 2. Les écrans

Chaque Raspberry Pi s'installe en « écran seul », exactement comme ci-dessus (commande `curl … | bash` affichée dans l'admin). Matériel conseillé pour un écran Full HD fluide : Raspberry Pi 4 (2 Go) ou Pi 5.

Options du player : `?screen=<nom>` pour identifier l'écran (ciblage des contenus et thème), `&lite=1` pour désactiver les animations (Raspberry Pi 3), `&theme=light`, `&theme=dark` ou `&theme=alternate` pour forcer un thème localement (prioritaire sur le réglage de l'admin).

## Conseils éditoriaux

- Un message par diapositive ; titre court (≤ 8 mots), texte de 2–3 lignes maximum : l'écran est lu à 3–5 m, en quelques secondes.
- Photos horizontales d'au moins 1920 px de large (sujet plutôt à droite : la gauche est fondue sous le texte) ; logos partenaires en PNG/SVG sur fond transparent.
- Affiches plein écran : format 1920 × 1080.
- Écrivez « Axians » et non « AXIANS » dans les textes.

### Vidéos

- Format conseillé : **MP4 (H.264), 1920 × 1080, 30 i/s, 4 à 8 Mbit/s**. Les Raspberry Pi 4/5 les lisent de façon fluide ; évitez la 4K et le HEVC.
- Les vidéos ne sont pas conservées dans le cache hors ligne : en cas de coupure réseau prolongée, une diapositive vidéo est sautée.
- Le son n'est joué que si la case « Activer le son » est cochée et que l'écran a des haut-parleurs (HDMI). Pensez aux espaces partagés : le muet est souvent préférable.

## Structure

```
app/
  app.py                 serveur Flask (API, admin, base SQLite)
  templates/             pages admin + player
  static/css/theme.css   couleurs et typographie
  static/css/display.css mise en page des écrans (canevas 1920×1080)
  static/css/admin.css   style de l'interface d'administration
  static/js/display.js   lecture de la playlist, vidéos, transitions, cache hors ligne
  static/img/            logo Axians (couleur et blanc)
  static/fonts/          police Inter (facultatif)
deploy/                  scripts Raspberry Pi et service systemd
install.sh               installation / mise à jour en une commande depuis GitHub
run.py                   lancement en production (Waitress)
```

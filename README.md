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
- **Entités** (Cloud, Communication…) : chaque diapositive peut être rattachée à une ou plusieurs entités, affichées en étiquettes colorées à côté du surtitre. La liste des entités se modifie dans l'onglet *Administration › Affichage*.
- **Compteur de jours automatique** (diapositive Chiffre clé, ex. « jours sans accident ») : on saisit la date du dernier événement, l'écran calcule et incrémente le nombre chaque jour, même hors ligne. Bouton « Remettre à zéro » dans la liste des contenus en cas de nouvel événement.
- **Disposition au choix** pour les diapositives Succès, Actualité, Partenaire et Événement : texte à gauche et visuel à droite, ou l'inverse (champ « Disposition » du formulaire).
- **Programmation** : dates de début et de fin, durée par diapositive, activation/désactivation, ordre de passage (↑ ↓ d'un cran, ⤒ ⤓ tout en haut ou tout en bas).
- **Ciblage des écrans** : un même serveur pour plusieurs écrans (`/display?screen=accueil`).
- **Thème clair ou sombre, écran par écran** : onglet *Administration › Écrans*. Chaque écran y apparaît automatiquement à sa première connexion, avec son état (en ligne / dernière connexion). On choisit pour chacun *Sombre*, *Clair*, *Alterné* (une diapositive sombre, une claire, avec fondu enchaîné) ou *Par défaut* (thème par défaut réglable dans *Administration › Affichage*). Le changement s'applique en moins d'une minute, sans toucher au Raspberry Pi.
- **Bandeau défilant « En bref »**, heure et date, barre de progression.
- **Robuste** : la playlist est mise en cache sur l'écran (continue de tourner si le réseau tombe), les modifications s'appliquent sans recharger la page, le texte trop long est réduit automatiquement, rechargement quotidien.
- **Aperçu** de chaque diapositive depuis l'admin ; flèche droite / espace pour passer à la suivante.
- **Dupliquer** un contenu : la copie (titre suivi de « (copie) ») est placée juste sous l'original, désactivée, et s'ouvre directement en modification ; on l'active une fois modifiée. L'image ou la vidéo est partagée avec l'original et n'est supprimée du disque que lorsque plus aucune diapositive ne l'utilise.
- **Sauvegarde et restauration** depuis l'admin : un fichier .zip regroupe contenus, images, vidéos, paramètres et thèmes des écrans ; l'import le réapplique (voir [Ce qui est conservé à chaque mise à jour](#ce-qui-est-conservé-à-chaque-mise-à-jour)).
- **E-mail à la publication** : chaque nouveau contenu est envoyé par e-mail, avec la même mise en forme que sur les écrans (logo, couleurs du type de contenu, photo, chiffre clé, date…), au moment où il commence à être diffusé. Les destinataires se règlent **par entité** (ex. Cloud, Communication) ; aperçu, test et envoi manuel depuis l'admin. Voir [E-mail à la publication](#e-mail-à-la-publication).
- **Utilisateurs et rôles** : chacun se connecte avec son identifiant. Les **administrateurs** ont accès à tout (paramètres, e-mail, écrans, mise à jour, sauvegarde, utilisateurs) ; les **éditeurs** gèrent uniquement les contenus. L'interface a deux onglets : *Contenus* (pour tous) et *Administration* (affichage, écrans, e-mail, utilisateurs, sauvegarde, mise à jour : administrateurs seulement). Voir [Utilisateurs](#utilisateurs).

## Démarrage rapide (poste de test)

```bash
git clone https://github.com/garg24/comaxians.git
cd comaxians
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
.venv\Scripts\python run.py
```

- Administration : http://localhost:8080/admin (identifiant `admin`, mot de passe par défaut : `axians`)
- Écran : http://localhost:8080/display

Variables d'environnement :

| Variable | Rôle | Défaut |
|---|---|---|
| `COMAXIANS_ADMIN_PASSWORD` | mot de passe d'installation du compte `admin` (voir [Utilisateurs](#utilisateurs)) | `axians` (**à changer**) |
| `COMAXIANS_SECRET_KEY` | clé de session | générée dans `data/.secret_key` |
| `COMAXIANS_DATA_DIR` | base SQLite + images téléversées | `./data` |
| `PORT` | port HTTP | `8080` |
| `COMAXIANS_MAX_UPLOAD_MB` | taille maximale d'un fichier téléversé | `500` |

## Mise en production

Trois scripts, selon le rôle du Raspberry Pi :

| Script | Rôle du Raspberry Pi | Installation |
|---|---|---|
| `deploy/install-serveur-et-ecran.sh` | **serveur + écran** (un seul Pi de ce type) – [option A](#option-a--le-raspberry-pi--serveur--écran-) | une commande, depuis GitHub (ou par archive sans Internet) |
| `deploy/install-serveur.sh` | **serveur seul**, sans écran sur ce Pi – [option B](#option-b--un-raspberry-pi--serveur-seul--sans-écran) | une commande, depuis GitHub (ou par archive sans Internet) |
| `deploy/install-ecran.sh` | **écran seul**, relié au serveur | une commande, depuis le serveur ou depuis GitHub |
| application `android/` (APK) | **écran seul** sur une **box Android** | voir [Écran sur box Android](#écran-sur-box-android--android-tv-apk) |

Un seul serveur suffit pour tous les écrans : choisissez l'option A **ou** l'option B, puis ajoutez autant d'écrans que nécessaire (Raspberry Pi ou box Android).

Les scripts qui configurent un écran adaptent automatiquement les réglages au **Raspberry Pi 3 B** (animations allégées `?lite=1`, sortie HDMI 720p, mémoire d'échange 512 Mo) et gardent les réglages normaux sur Pi 4 / Pi 5. Tous les scripts acceptent `--dry-run` (simulation sans rien modifier) et `--help`.

Prérequis sur chaque Pi qui affiche : Raspberry Pi OS **Desktop** (Bookworm ou plus récent) installé avec Raspberry Pi Imager, SSH activé, connexion automatique au bureau (`sudo raspi-config` → System Options → Auto Login → Desktop). Pour un Pi « serveur seul », Raspberry Pi OS **Lite** (sans bureau) suffit, avec SSH activé.

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

**Mise à jour depuis l'admin** : onglet *Administration › Mise à jour*. À l'ouverture, l'application interroge GitHub et liste les nouveautés (bouton « Rechercher une mise à jour » pour recommencer). « Mettre à jour maintenant » télécharge puis installe la nouvelle version, sans commande à taper ; l'avancement s'affiche en direct (étapes, barre de progression, journal détaillé en cas de problème). Vos contenus sont conservés et une sauvegarde est faite avant. Ce bouton apparaît après une première mise à jour faite avec la commande ci-dessous. Il ne fonctionne pas pour une installation par archive.

**Mise à jour en ligne de commande** : relancez simplement la commande (le code est mis à jour par `git pull`, vos contenus sont conservés) :

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
- installe le serveur dans `/opt/comaxians` comme **service systemd** démarré au boot (en appelant `install-serveur.sh` ; utilisateur dédié, données dans `/var/lib/comaxians`, mot de passe dans `/etc/comaxians.env` lisible par root seulement) ;
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
| Mot de passe admin | conservé (sauf si `COMAXIANS_ADMIN_PASSWORD=…` est fourni : il devient celui du compte `admin`) |
| Utilisateurs et leurs mots de passe | **conservés** |

Juste avant de remplacer l'application, le script fait une **sauvegarde automatique** (`comaxians-avant-maj-….tar.gz`, conservée 90 jours). Si la sauvegarde échoue, la mise à jour s'arrête sans rien modifier.

**Revenir en arrière** (après une erreur de manipulation ou une mise à jour) :

```bash
sudo comaxians-restore                                   # liste les sauvegardes disponibles
sudo comaxians-restore /var/backups/comaxians/comaxians-avant-maj-20261106-103000.tar.gz
```

L'état actuel est lui-même sauvegardé (`comaxians-avant-restauration-…`) avant la restauration ; le serveur est arrêté puis relancé automatiquement.

**Sans ligne de commande : onglet *Administration › Sauvegarde*.**
- *Télécharger la sauvegarde* : un fichier `axians-affichage-AAAA-MM-JJ-HHhMM.zip` avec tous les contenus, leurs images et vidéos, les paramètres (dont les destinataires des e-mails) et les thèmes des écrans (ni les comptes utilisateurs et leurs mots de passe, ni celui du serveur d'envoi des e-mails, ni la clé de session). Il contient les dates d'anniversaire et des adresses e-mail : à conserver en lieu sûr.
- *Importer et remplacer* : remplace tous les contenus, paramètres et thèmes des écrans par ceux du fichier. Le fichier est entièrement vérifié avant toute modification (format, version, fichiers endommagés, place disponible) ; en cas de problème, rien n'est modifié. Une copie de l'état précédent est faite juste avant et reste téléchargeable dans la même section (les 2 plus récentes sont gardées, dans `/var/lib/comaxians/exports`). Les utilisateurs, l'activation de l'envoi des e-mails et le mot de passe SMTP restent ceux de ce serveur ; les contenus du fichier déjà en diffusion ne sont pas envoyés par e-mail.
- Utile pour recopier l'affichage sur un autre serveur, ou le reconstruire après une réinstallation. Taille maximale d'un import : `COMAXIANS_MAX_UPLOAD_MB` (500 Mo par défaut).

> Les contenus d'exemple ne sont créés qu'à la toute première installation : si vous supprimez toutes les diapositives, la liste reste vide après un redémarrage ou une mise à jour.

> Pensez à réserver l'adresse IP du Pi dans votre box/routeur, et à garder de temps en temps une sauvegarde hors du Pi (« Télécharger la sauvegarde » dans l'admin, ou `scp pi@IP-DU-PI:/var/backups/comaxians/*.tar.gz .`) : une carte SD peut lâcher.

### Option B – Un Raspberry Pi « serveur seul » (sans écran)

Pour héberger le serveur sur un Pi dédié (local technique, baie…) sans écran branché : tous les écrans sont alors des Raspberry Pi « écran seul » ou des box Android. Un Raspberry Pi 3 B suffit largement pour ce rôle.

**Directement depuis GitHub** (recommandé) :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --serveur-seul
```

Aucun redémarrage n'est nécessaire. À la fin, le script affiche l'adresse de l'admin (`http://IP-DU-PI:8080/admin`), la commande qui équipe les Pi écrans et le lien de l'APK pour les box Android.

**Par une archive** (Pi sans Internet) : préparez et copiez l'archive comme à la [méthode 2 de l'option A](#méthode-2--par-une-archive-préparée-sur-le-pc-pi-sans-internet), puis sur le Pi :

```bash
mkdir -p ~/comaxians && tar -xzf ~/comaxians-pi.tar.gz -C ~/comaxians
bash ~/comaxians/deploy/install-serveur.sh
```

Le script installe exactement la partie serveur de l'option A : service systemd, données dans `/var/lib/comaxians`, sauvegarde quotidienne, commandes `comaxians-backup` / `comaxians-restore`, publication du script écran sur `http://IP-DU-PI:8080/install-ecran.sh`. Il ne configure aucun affichage sur ce Pi (ni navigateur, ni résolution HDMI, ni redémarrage quotidien). La reprise de vos contenus (`--import-data ~/comaxians-data`), la conservation des contenus à chaque mise à jour et la restauration fonctionnent comme pour l'option A.

**Mise à jour** : relancez la même commande. Même sans `--serveur-seul`, `install.sh` reconnaît un Pi installé en « serveur seul » et ne lui ajoute pas d'écran. Pour brancher plus tard un écran sur ce même Pi : `install.sh … | bash -s -- --tout-en-un --screen accueil`.

Options : `--import-data DOSSIER`, `--no-import`, `--dry-run` (simulation sans rien modifier), `--help`.

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

### Écran sur box Android / Android TV (APK)

Une box Android peut remplacer un Raspberry Pi « écran seul » grâce à l'application **Axians Affichage** (dossier `android/`). Elle affiche la page `/display` du serveur en plein écran ; le serveur, lui, reste sur le Raspberry Pi.

**Télécharger l'APK** : https://github.com/garg24/comaxians/releases/latest/download/comaxians-ecran.apk

**Installer sur la box**
- avec l'application *Downloader* (Android TV) ou le navigateur de la box : ouvrez le lien ci-dessus, puis autorisez l'installation d'applications inconnues quand Android le demande ;
- ou depuis un PC, avec ADB : `adb install comaxians-ecran.apk`.

**Premier lancement** : l'écran de réglages s'ouvre (navigable à la télécommande).
1. *Adresse du serveur* : l'adresse IP du Raspberry Pi serveur, par ex. `192.168.1.20` (port 8080 ajouté automatiquement) ;
2. *Nom de cet écran* : par ex. `hall` – il apparaîtra dans l'admin (section Écrans) pour choisir son thème ;
3. *Tester la connexion*, puis *Enregistrer et afficher*.

Options : animations allégées (box peu puissante), démarrage automatique, mode « écran d'accueil ».

**Démarrage automatique** : activé par défaut. Sur Android 10 et plus, Android bloque l'ouverture d'une application au démarrage ; deux solutions, proposées dans les réglages :
- le bouton *Autoriser le démarrage automatique* (autorisation « afficher par-dessus les autres applications ») ;
- ou l'option *Utiliser comme écran d'accueil* : la box ouvre l'affichage à chaque démarrage et à chaque appui sur la touche Accueil (le plus fiable sur les box dédiées).

**Télécommande** : touche *Retour* ou *Menu* = réglages ; flèche droite = diapositive suivante. Les réglages se referment seuls au bout de 3 minutes.

**Réglage à distance, sans télécommande** (ADB) :

```bash
adb shell am start -n io.github.garg24.comaxians.ecran/.MainActivity --es server 192.168.1.20 --es screen hall
```

**Moteur web** : l'écran utilise le moteur web d'Android (« Android System WebView »), dont la version est affichée en bas des réglages. À partir de Chrome 111, l'affichage est complet ; une version plus ancienne fonctionne avec un affichage simplifié (sans halos lumineux). Sur les box non mises à jour, installez la dernière version d'*Android System WebView* depuis le Play Store.

**Mises à jour** : installez la nouvelle APK par-dessus l'ancienne, les réglages sont conservés.

**Compilation** (pour information) : l'APK est fabriquée par GitHub Actions (`.github/workflows/ecran-android.yml`) à chaque modification du dossier `android/`. Elle est d'abord testée sur un émulateur Android face au vrai serveur, puis publiée sous la version `ecran-latest`. Elle est signée avec une clé conservée dans les secrets du dépôt (`ECRAN_KEYSTORE_B64`, `ECRAN_KEYSTORE_PASSWORD`). Sans ces secrets, la compilation produit une APK de test qui n'est pas publiée.

### Désinstaller ou réinstaller à zéro

Le script `deploy/desinstaller.sh` retire tout ce que les scripts d'installation ont ajouté. Il détecte seul les rôles présents (serveur, écran ou les deux), affiche ce qu'il va supprimer et demande de taper `SUPPRIMER` pour confirmer. Il fonctionne aussi sur un Pi « écran seul » ou « serveur seul » (sur ce dernier, le redémarrage final est inutile).

**Désinstaller** (vos contenus sont d'abord sauvegardés dans `/var/backups/comaxians`, qui est conservé) :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/deploy/desinstaller.sh | bash
sudo reboot
```

**Tout effacer, sauvegardes comprises** (irréversible) :

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/deploy/desinstaller.sh | bash -s -- --tout
sudo reboot
```

| Supprimé | Sans option | Avec `--tout` |
|---|---|---|
| Service serveur, application (`/opt/comaxians`), mot de passe et configuration | ✔ | ✔ |
| Contenus et images (`/var/lib/comaxians`) | ✔ après sauvegarde | ✔ sans sauvegarde |
| Outils et planification des sauvegardes, utilisateur `comaxians` | ✔ | ✔ |
| Affichage au démarrage (kiosque), redémarrage quotidien, résolution HDMI imposée | ✔ (la mise en veille de l'écran est réactivée) | ✔ |
| Code téléchargé (`~/comaxians`) | ✔ | ✔ |
| Sauvegardes (`/var/backups/comaxians`) | conservées | ✔ |
| Copies (`~/comaxians-data`, `~/comaxians.ancien-*`, `~/comaxians-pi.tar.gz`) | conservées | ✔ |

Les logiciels installés (Chromium, police Inter, Python, Git…) et la taille de la mémoire d'échange restent en place : ils peuvent servir à d'autres usages du Pi.

Autres options : `--serveur` (retire seulement le serveur), `--ecran` (retire seulement l'affichage), `--oui` (sans confirmation, pour un usage automatisé), `--dry-run` (simulation), `--help`.

**Réinstaller à zéro**, en écrasant toute la configuration précédente (mot de passe, écrans, paramètres, contenus) : désinstallez, puis réinstallez. Le mot de passe admin est redemandé et les contenus d'exemple sont recréés.

```bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/deploy/desinstaller.sh | bash
curl -fsSL https://raw.githubusercontent.com/garg24/comaxians/main/install.sh | bash -s -- --screen accueil
sudo reboot
```

Pi « serveur seul » : remplacez `--screen accueil` par `--serveur-seul` (et sautez le redémarrage).

Pour récupérer ensuite les contenus d'avant la réinstallation, restaurez la sauvegarde faite par la désinstallation :

```bash
sudo comaxians-restore      # liste les sauvegardes : choisissez comaxians-avant-desinstallation-…
```

Ajoutez `--tout` à la première commande pour repartir d'un Pi totalement vierge (sauvegardes comprises).

### Option C – Serveur sur une autre machine (VM, NAS…)

#### 1. Le serveur

Sur une VM Linux ou un NAS (pour un Raspberry Pi dédié, préférez l'[option B](#option-b--un-raspberry-pi--serveur-seul--sans-écran)) :

```bash
sudo useradd -r -m -d /opt/comaxians comaxians
sudo -u comaxians git clone https://github.com/garg24/comaxians.git /opt/comaxians
cd /opt/comaxians && sudo -u comaxians python3 -m venv .venv && sudo -u comaxians .venv/bin/pip install -r requirements.txt
sudo -u comaxians .venv/bin/pip install Pillow   # facultatif : images des e-mails (WebP converti, photos allégées)
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

## E-mail à la publication

Onglet *Administration › E-mail* :

1. **Serveur d'envoi (SMTP)**
   - *Microsoft 365* : `smtp.office365.com`, STARTTLS, port 587, avec l'identifiant et le mot de passe d'une boîte autorisée à envoyer en SMTP (l'« authentification SMTP » doit être activée pour cette boîte par l'administrateur Microsoft 365). L'adresse d'expédition est celle de cette boîte.
   - *Relais SMTP de l'entreprise* : son nom, sécurité « Aucune », port 25, sans identifiant (le relais doit accepter l'adresse IP du serveur).
   - Le mot de passe n'est jamais réaffiché ni inclus dans les sauvegardes. Il peut aussi être placé sur le serveur, dans `/etc/comaxians.env` : `COMAXIANS_SMTP_PASSWORD=…` (prioritaire), puis `sudo systemctl restart comaxians`.
2. **Destinataires par entité** : une liste par entité déclarée dans « Entités de la société » (ex. Cloud, Communication), plus une liste « Contenus sans entité ». Une adresse par ligne ou séparées par des virgules ; une liste copiée depuis Outlook (« Dupont, Jean <jean.dupont@…> ; … ») convient.
3. Cocher **« Envoyer automatiquement les nouveaux contenus par e-mail »**, puis **« Enregistrer et envoyer un test »** vers votre adresse.

Quand part l'e-mail ?
- **Une seule fois par contenu, quand il commence à être diffusé** : 2 minutes après son enregistrement (le temps de corriger une coquille), quand on active un contenu désactivé, ou le jour de sa date de début à partir de 8 h.
- Aux adresses de **toutes ses entités** (une personne présente dans plusieurs listes ne le reçoit qu'une fois), en copie cachée ; un contenu sans entité part à la liste « Contenus sans entité ».
- Pas d'e-mail pour les anniversaires, ni pour un contenu dont la case « Envoyer par e-mail à la publication » est décochée (elle l'est par défaut pour les vidéos). Les contenus déjà en diffusion quand on active l'envoi, ou importés d'une sauvegarde, ne sont pas envoyés.
- Une modification après l'envoi ne renvoie pas l'e-mail ; une copie (« Dupliquer ») est un nouveau contenu, envoyé à sa publication.

La liste des contenus indique l'état de chacun (*✉ Envoi prévu à 10:42*, *✉ Envoyé le 23/11 à 10:42*, *✉ Envoi le 25/11 à 8 h*…). Un clic ouvre l'**aperçu de l'e-mail** (version ordinateur ou téléphone), avec les destinataires, un envoi de test vers votre adresse et l'envoi manuel (ou le renvoi). En cas d'échec (serveur injoignable, mot de passe refusé…), l'erreur s'affiche dans la section et dans la liste, et l'envoi est retenté automatiquement, de plus en plus espacé (jusqu'à toutes les 30 minutes).

Mise en forme : thème sombre ou clair au choix, compatible Outlook, Gmail et téléphones. Les photos sont jointes au message (converties en JPEG et allégées, portrait de l'employé du mois arrondi) grâce au module Python **Pillow**, installé automatiquement par le script d'installation ou de mise à jour ; sans lui, tout fonctionne mais les images WebP ne sont pas jointes. Les images SVG ne sont pas jointes (ni Outlook ni Gmail ne les affichent) ; une vidéo est remplacée par une invitation à la regarder sur les écrans.

## Utilisateurs

Chacun se connecte avec son **identifiant** et son mot de passe. Deux profils :

| | Administrateur | Éditeur |
|---|---|---|
| Contenus : créer, modifier, dupliquer, ordre, activer, supprimer, aperçus | ✔ | ✔ |
| Aperçu de l'e-mail d'un contenu, test, envoi manuel | ✔ | ✔ |
| Paramètres des écrans (bandeau « En bref », durée, thème, entités) | ✔ | |
| E-mail à la publication (serveur SMTP, destinataires) | ✔ | |
| Écrans, mise à jour, sauvegarde et restauration | ✔ | |
| Utilisateurs : créer, modifier, réinitialiser un mot de passe, supprimer | ✔ | |

- **Premier administrateur** : le compte `admin`, avec le mot de passe choisi à l'installation (`COMAXIANS_ADMIN_PASSWORD`, `axians` par défaut). Il reste toujours administrateur et ne peut pas être supprimé. À la mise à jour d'une installation existante, il est créé avec le mot de passe actuel : rien ne change pour se connecter, à part l'identifiant `admin` à saisir.
- **Onglet *Administration › Utilisateurs*** : ajouter un compte (identifiant, nom, mot de passe de 8 caractères minimum, rôle), le modifier (nom, rôle, nouveau mot de passe) ou le supprimer. Un administrateur ne peut ni changer son propre rôle ni supprimer son propre compte.
- **Mon compte** (nom en haut à droite) : chacun change son nom affiché et son mot de passe (le mot de passe actuel est demandé). Un changement de mot de passe ferme les autres sessions ouvertes avec ce compte ; un rôle changé ou un compte supprimé s'applique immédiatement.
- **Mot de passe du compte `admin` perdu** : sur le serveur, changez le mot de passe d'installation (ligne `COMAXIANS_ADMIN_PASSWORD="…"` de `/etc/comaxians.env`, avec `sudo nano /etc/comaxians.env`), puis `sudo systemctl restart comaxians` ; ou relancez la commande d'installation précédée du nouveau mot de passe (`curl -fsSL …/install.sh | COMAXIANS_ADMIN_PASSWORD='nouveau-mot-de-passe' bash -s -- --screen accueil`). Au redémarrage, le compte `admin` reprend ce mot de passe et redevient administrateur ; les autres comptes ne changent pas.
- Après 10 échecs de connexion en 15 minutes depuis un même poste, la connexion est refusée quelques minutes.
- Les mots de passe sont enregistrés hachés (scrypt). Les comptes ne font pas partie de la sauvegarde (.zip) et ne sont pas remplacés par un import.

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
  templates/mail/        e-mail envoyé à la publication d'un contenu
  static/css/theme.css   couleurs et typographie
  static/css/display.css mise en page des écrans (canevas 1920×1080)
  static/css/admin.css   style de l'interface d'administration
  static/js/display.js   lecture de la playlist, vidéos, transitions, cache hors ligne
  static/img/            logo Axians (couleur et blanc ; PNG pour les e-mails)
  static/fonts/          police Inter (facultatif)
deploy/                  scripts Raspberry Pi (installation serveur + écran, serveur seul, écran seul,
                         désinstallation)
                         et service systemd
install.sh               installation / mise à jour en une commande depuis GitHub
android/                 application Android « Axians Affichage » (écran pour box Android / Android TV)
.github/workflows/       compilation, test sur émulateur et publication de l'APK
run.py                   lancement en production (Waitress)
```

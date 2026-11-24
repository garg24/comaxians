"""ComAxians – affichage dynamique interne aux couleurs d'Axians.

Un serveur central (cette application) héberge :
  * /admin   : interface de gestion des contenus (succès, actualités, partenaires…)
  * /display : le "player" plein écran ouvert par chaque Raspberry Pi en mode kiosque
  * /api/playlist : la liste des diapositives actives, interrogée périodiquement par les écrans
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import smtplib
import socket
import sqlite3
import ssl
import struct
import tempfile
import threading
import uuid
import zipfile
from datetime import date, datetime, time as dtime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from functools import wraps
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

from flask import (Flask, abort, flash, g, has_request_context, jsonify, redirect, render_template,
                   request, send_file, send_from_directory, session, url_for)
from markupsafe import Markup, escape
from werkzeug.security import check_password_hash, generate_password_hash

try:   # facultatif : images des e-mails converties (WebP → JPEG/PNG), allégées, portrait rond
    from PIL import Image, ImageDraw, ImageOps
except ImportError:
    Image = None

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("COMAXIANS_DATA_DIR", BASE_DIR.parent / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "comaxians.db"
IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif", "svg"}
VIDEO_EXT = {"mp4", "webm", "m4v", "mov"}
ALLOWED_EXT = IMAGE_EXT | VIDEO_EXT

SLIDE_TYPES = {
    "succes": "Succès",
    "actualite": "Actualité",
    "partenaire": "Partenaire",
    "evenement": "Événement",
    "employe": "Employé du mois",
    "anniversaire": "Anniversaires",
    "chiffre": "Chiffre clé",
    "message": "Message",
    "video": "Vidéo",
    "image": "Affiche plein écran",
}
# Types pour lesquels le titre est facultatif (le média se suffit à lui-même)
TITLE_OPTIONAL = {"video", "image"}
# Types composés d'un texte et d'un visuel, dont on peut choisir la disposition
LAYOUT_TYPES = {"succes", "actualite", "partenaire", "evenement", "employe"}
LAYOUTS = {"right": "Visuel à droite", "left": "Visuel à gauche"}


def media_type(name: str) -> str:
    if not name:
        return ""
    return "video" if name.rsplit(".", 1)[-1].lower() in VIDEO_EXT else "image"

DEFAULT_SETTINGS = {
    "site_name": "Axians",
    "ticker": "",
    "default_duration": "12",
    "show_clock": "1",
    "show_ticker": "1",
    "default_theme": "dark",
    "entities": "Cloud\nCommunication",
    # E-mail à la publication (section « E-mail à la publication » de l'admin)
    "mail_enabled": "0",
    "mail_recipients": "{}",      # {"Cloud": ["a@axians.fr", …], "": [contenus sans entité]}
    "mail_theme": "dark",
    "mail_from": "",
    "mail_from_name": "",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_security": "starttls",
    "smtp_user": "",
}
# Réglages propres à ce serveur : jamais dans une sauvegarde, jamais remplacés par un import
LOCAL_SETTINGS = {"smtp_password", "mail_enabled", "mail_status", "admin_env_hash"}
# Jamais transmis aux pages de l'admin
SECRET_SETTINGS = {"smtp_password", "admin_env_hash"}
# Couleurs attribuées aux entités, dans l'ordre de la liste
ENTITY_COLORS = ["#22D3EE", "#F59E0B", "#22C55E", "#A855F7", "#FF5A5F", "#2F6BFF"]
THEMES = {"dark": "Sombre", "light": "Clair", "alternate": "Alterné"}
THEME_ICONS = {"dark": "🌙", "light": "☀️", "alternate": "◐"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _secret_key() -> str:
    env = os.environ.get("COMAXIANS_SECRET_KEY")
    if env:
        return env
    key_file = DATA_DIR / ".secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_hex(32))
    return key_file.read_text().strip()


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_secret_key(),
    MAX_CONTENT_LENGTH=int(os.environ.get("COMAXIANS_MAX_UPLOAD_MB", 500)) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
# Mot de passe de l'installation : celui du compte administrateur d'origine (identifiant BASE_ADMIN)
ADMIN_PASSWORD = os.environ.get("COMAXIANS_ADMIN_PASSWORD", "axians")
BASE_ADMIN = "admin"


# --------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS slides (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    kicker      TEXT DEFAULT '',
    title       TEXT NOT NULL,
    body        TEXT DEFAULT '',
    highlight   TEXT DEFAULT '',
    highlight_label TEXT DEFAULT '',
    image       TEXT DEFAULT '',
    sound       INTEGER DEFAULT 0,
    layout      TEXT DEFAULT 'right',
    counter_date TEXT DEFAULT '',
    entities    TEXT DEFAULT '',
    people      TEXT DEFAULT '',
    duration    INTEGER DEFAULT 0,
    start_date  TEXT DEFAULT '',
    end_date    TEXT DEFAULT '',
    screens     TEXT DEFAULT '',
    active      INTEGER DEFAULT 1,
    position    INTEGER DEFAULT 0,
    updated_at  TEXT,
    mail_on_publish INTEGER DEFAULT 1,
    mailed_at   TEXT DEFAULT '',
    mail_note   TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS screens (
    name      TEXT PRIMARY KEY,
    theme     TEXT DEFAULT '',
    last_seen TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name       TEXT DEFAULT '',
    role       TEXT NOT NULL DEFAULT 'editor',
    password   TEXT NOT NULL,
    builtin    INTEGER DEFAULT 0,
    token      TEXT NOT NULL,
    created_at TEXT DEFAULT '',
    last_login TEXT DEFAULT ''
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    # Les contenus d'exemple ne sont créés qu'avec une base toute neuve : une liste vidée
    # volontairement reste vide après un redémarrage ou une mise à jour.
    db_file = Path(DB_PATH)
    new_database = not db_file.exists() or db_file.stat().st_size == 0
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    # Migration des bases créées avant l'ajout de la vidéo
    cols = {r[1] for r in db.execute("PRAGMA table_info(slides)")}
    if "sound" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN sound INTEGER DEFAULT 0")
    if "layout" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN layout TEXT DEFAULT 'right'")
    if "counter_date" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN counter_date TEXT DEFAULT ''")
    if "entities" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN entities TEXT DEFAULT ''")
    if "people" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN people TEXT DEFAULT ''")
    if "mail_on_publish" not in cols:   # e-mail à la publication (rien n'est envoyé tant qu'il n'est pas activé)
        db.execute("ALTER TABLE slides ADD COLUMN mail_on_publish INTEGER DEFAULT 1")
        db.execute("UPDATE slides SET mail_on_publish=0 WHERE type IN ('video', 'anniversaire')")
    if "mailed_at" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN mailed_at TEXT DEFAULT ''")
    if "mail_note" not in cols:
        db.execute("ALTER TABLE slides ADD COLUMN mail_note TEXT DEFAULT ''")
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    if new_database:
        _seed(db)
    _sync_base_admin(db)
    db.commit()
    db.close()


def _sync_base_admin(db: sqlite3.Connection):
    """Compte « admin » d'origine, premier administrateur : créé au premier démarrage avec le mot de passe
    de l'installation (COMAXIANS_ADMIN_PASSWORD), puis modifiable dans l'admin. S'il est perdu, relancer
    l'installation avec un nouveau COMAXIANS_ADMIN_PASSWORD redonne ce mot de passe au compte."""
    row = db.execute("SELECT value FROM settings WHERE key='admin_env_hash'").fetchone()
    base = db.execute("SELECT id FROM users WHERE builtin=1").fetchone()
    if base and row and row[0] and check_password_hash(row[0], ADMIN_PASSWORD):
        return   # mot de passe de l'installation inchangé : le compte garde le sien
    pw = generate_password_hash(ADMIN_PASSWORD)
    if not base:
        base = db.execute("SELECT id FROM users WHERE username=?", (BASE_ADMIN,)).fetchone()
    if base:
        db.execute("UPDATE users SET password=?, role='admin', builtin=1, token=? WHERE id=?",
                   (pw, secrets.token_hex(16), base[0]))
    else:
        db.execute("INSERT INTO users(username, name, role, password, builtin, token, created_at) "
                   "VALUES (?, ?, 'admin', ?, 1, ?, ?)",
                   (BASE_ADMIN, "Administrateur", pw, secrets.token_hex(16),
                    datetime.now().isoformat(timespec="seconds")))
    db.execute("INSERT INTO settings(key, value) VALUES ('admin_env_hash', ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (pw,))


MONTHS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
             "août", "septembre", "octobre", "novembre", "décembre"]


def month_label(d: date) -> str:
    """« Novembre 2026 »"""
    return f"{MONTHS_FR[d.month - 1].capitalize()} {d.year}"


def _seed(db: sqlite3.Connection):
    """Contenus d'exemple créés à la toute première installation (un par grand type de contenu).
    Les personnes citées sont fictives ; les dates sont calculées à partir du jour d'installation."""
    now = datetime.now().isoformat(timespec="seconds")
    today = date.today()
    event_day = today + timedelta(days=21)
    birthdays = [   # Léa est fêtée le jour de l'installation, pour voir la diapositive tout de suite
        {"first": "Léa", "last": "Bernard", "day": today.day, "month": today.month},
        {"first": "Hugo", "last": "Petit", "day": 14, "month": 3},
        {"first": "Nina", "last": "Robert", "day": 2, "month": 9},
    ]
    if today.month == 2 and today.day == 29:          # date impossible à fêter les autres années
        birthdays[0]["day"] = 28
    birthdays.sort(key=lambda p: (p["month"], p["day"]))
    demo = [
        dict(type="succes", kicker="Nouveau contrat", title="La Métropole nous confie son réseau fibre",
             body="Déploiement et maintenance de 450 km de fibre optique pour connecter les bâtiments publics du territoire.",
             highlight="450 km", highlight_label="de fibre déployée", entities="Communication"),
        dict(type="chiffre", kicker="Sécurité", title="Jours sans accident",
             body="Merci à toutes et à tous pour votre vigilance au quotidien sur nos chantiers.",
             highlight_label="jours consécutifs",
             # compteur automatique : 312 aujourd'hui, puis +1 chaque jour
             counter_date=(today - timedelta(days=312)).isoformat()),
        dict(type="actualite", kicker="Vie de l'agence", title="Bienvenue aux nouveaux arrivants",
             body="Ce mois-ci, 6 collaborateurs ont rejoint nos équipes Cloud, Cybersécurité et Réseaux. "
                  "Réservez-leur un bel accueil !",
             entities="Cloud,Communication"),
        dict(type="employe", kicker="Employé du mois", title="Camille Martin",
             highlight=month_label(today), highlight_label="Cheffe de projet Cloud",
             body="Pour son engagement exemplaire sur la migration de nos clients vers le cloud, "
                  "livrée dans les temps et saluée par tous.",
             entities="Cloud"),
        dict(type="partenaire", kicker="Partenaire", title="Partenariat Gold renouvelé",
             body="Notre expertise reconnue sur les infrastructures datacenter et la sécurité réseau.",
             entities="Cloud"),
        dict(type="evenement", kicker="Agenda", title="Afterwork Innovation",
             body="Démonstrations IoT & IA par nos équipes – Espace Atrium, 18h00",
             highlight=str(event_day.day), highlight_label=MONTHS_FR[event_day.month - 1]),
        dict(type="anniversaire", kicker="Anniversaires", title="Joyeux anniversaire !",
             body="Toute l'équipe vous souhaite une excellente journée !",
             people=json.dumps(birthdays, ensure_ascii=False)),
        dict(type="message", kicker="Nos valeurs", title="The best of ICT with a human touch"),
    ]
    for i, s in enumerate(demo):
        s = {"body": "", "highlight": "", "highlight_label": "", "counter_date": "", "entities": "",
             "people": ""} | s | {"position": i, "updated_at": now}
        cols = ", ".join(s)
        db.execute(f"INSERT INTO slides({cols}) VALUES ({', '.join('?' * len(s))})", list(s.values()))


def get_settings() -> dict:
    rows = get_db().execute("SELECT key, value FROM settings").fetchall()
    s = dict(DEFAULT_SETTINGS)
    s.update({r["key"]: r["value"] for r in rows})
    return s


# ------------------------------------------------------------------------- auth
# Deux profils : les administrateurs ont accès à tout (paramètres, e-mail, écrans, mise à jour, sauvegarde,
# utilisateurs) ; les éditeurs gèrent uniquement les contenus.
ROLES = {"admin": "Administrateur", "editor": "Éditeur"}
ROLE_HELP = {"admin": "Contenus, paramètres, e-mail, écrans, mise à jour, sauvegarde et utilisateurs",
             "editor": "Contenus uniquement : création, modification, ordre, activation, suppression"}
PASSWORD_MIN = 8
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._@-]{1,39}$")
LOGIN_MAX_FAILS, LOGIN_WINDOW = 10, 900       # au-delà de 10 échecs en 15 min, connexion refusée un moment
_login_fails: dict[str, list[float]] = {}
_login_lock = threading.Lock()
_dummy_hash: list[str] = []


def current_user() -> sqlite3.Row | None:
    """Compte connecté, relu en base à chaque requête : un compte supprimé, un rôle ou un mot de passe
    changé s'appliquent tout de suite (le jeton change avec le mot de passe : autres sessions fermées)."""
    if "user" not in g:
        user = None
        if session.get("uid"):
            r = get_db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
            if r and secrets.compare_digest(r["token"], str(session.get("tok", ""))):
                user = r
        g.user = user
    return g.user


def is_admin() -> bool:
    u = current_user()
    return bool(u and u["role"] == "admin")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Réservé aux administrateurs (paramètres, e-mail, écrans, mise à jour, sauvegarde, utilisateurs)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        if not is_admin():
            if request.endpoint in ("update_check", "update_state"):
                return jsonify({"error": "Réservé aux administrateurs"}), 403
            flash("Réservé aux administrateurs : votre compte permet de gérer les contenus.", "error")
            return redirect(url_for("admin"))
        return view(*args, **kwargs)
    return wrapped


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["LAYOUTS"] = LAYOUTS
app.jinja_env.globals["LAYOUT_TYPES"] = sorted(LAYOUT_TYPES)


@app.template_filter("from_json")
def _from_json(value):
    try:
        return json.loads(value or "[]")
    except (TypeError, ValueError):
        return []


def entity_list(settings: dict | None = None) -> list[dict]:
    """Entités de l'entreprise (paramétrables dans l'admin), avec leur couleur."""
    raw = (settings or get_settings()).get("entities", "")
    names = []
    for line in raw.splitlines():
        n = line.strip()[:30]
        if n and n.lower() not in (x.lower() for x in names):
            names.append(n)
    return [{"name": n, "color": ENTITY_COLORS[i % len(ENTITY_COLORS)]} for i, n in enumerate(names[:8])]


def slide_entities(r, entities: list[dict]) -> list[dict]:
    """Entités cochées pour une diapositive, dans l'ordre de la liste (les entités supprimées sont ignorées)."""
    chosen = {e.strip().lower() for e in (r["entities"] or "").split(",") if e.strip()}
    return [e for e in entities if e["name"].lower() in chosen]


@app.context_processor
def inject_entities():
    # Hors requête (e-mail rendu par la tâche de fond) : inutile
    if not has_request_context() or request.endpoint in ("display", "uploaded", "static"):
        return {"ENTITIES": [], "current_user": None, "is_admin": False}
    return {"ENTITIES": entity_list(), "current_user": current_user(), "is_admin": is_admin()}


@app.before_request
def check_csrf():
    if request.method == "POST" and request.endpoint != "login":
        if request.form.get("csrf") != session.get("csrf"):
            abort(400, "Jeton CSRF invalide")


def _login_wait(ip: str) -> int:
    """Minutes d'attente imposées après trop d'échecs de connexion depuis cette adresse (0 : aucune)."""
    now = time.time()
    with _login_lock:
        fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_WINDOW]
        if fails:
            _login_fails[ip] = fails
        else:
            _login_fails.pop(ip, None)
        if len(fails) < LOGIN_MAX_FAILS:
            return 0
        return max(1, -(-int(fails[0] + LOGIN_WINDOW - now) // 60))


def _login_failed(ip: str):
    with _login_lock:
        _login_fails.setdefault(ip, []).append(time.time())


def check_password(user: sqlite3.Row | None, password: str) -> bool:
    if not _dummy_hash:
        _dummy_hash.append(generate_password_hash(secrets.token_hex(8)))
    # Identifiant inconnu : même temps de calcul, pour ne pas révéler quels comptes existent
    ok = check_password_hash(user["password"] if user else _dummy_hash[0], password)
    return bool(user) and ok


def password_error(password: str, confirm: str | None = None) -> str:
    if len(password) < PASSWORD_MIN:
        return f"Mot de passe trop court ({PASSWORD_MIN} caractères minimum)."
    if len(password) > 200:
        return "Mot de passe trop long (200 caractères maximum)."
    if confirm is not None and password != confirm:
        return "Les deux mots de passe saisis sont différents."
    return ""


@app.route("/login", methods=["GET", "POST"])
def login():
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()[:80]
        ip = request.remote_addr or "?"
        wait = _login_wait(ip)
        if wait:
            flash(f"Trop d'essais de connexion : réessayez dans {wait} minute{'s' if wait > 1 else ''}.", "error")
        else:
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if check_password(user, request.form.get("password", "")):
                session.clear()
                session["uid"], session["tok"] = user["id"], user["token"]
                db.execute("UPDATE users SET last_login=? WHERE id=?",
                           (datetime.now().isoformat(timespec="seconds"), user["id"]))
                db.commit()
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("admin"))
            _login_failed(ip)
            flash("Identifiant ou mot de passe incorrect.", "error")
    return render_template("login.html", username=username)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ helpers
def is_live(slide: sqlite3.Row, today: str) -> bool:
    if not slide["active"]:
        return False
    if slide["start_date"] and slide["start_date"] > today:
        return False
    if slide["end_date"] and slide["end_date"] < today:
        return False
    return True


def targets_screen(slide: sqlite3.Row, screen: str) -> bool:
    wanted = [s.strip().lower() for s in (slide["screens"] or "").split(",") if s.strip()]
    return not wanted or (screen and screen.lower() in wanted)


def slide_to_dict(r: sqlite3.Row, default_duration: int, entities: list[dict] | None = None) -> dict:
    return {
        "entities": slide_entities(r, entities or []),
        "id": r["id"],
        "type": r["type"],
        "kicker": r["kicker"],
        "title": r["title"],
        "body": r["body"],
        "highlight": r["highlight"],
        "highlight_label": r["highlight_label"],
        "media": url_for("uploaded", name=r["image"]) if r["image"] else "",
        "media_type": media_type(r["image"]),
        "sound": bool(r["sound"]),
        "layout": r["layout"] if r["layout"] in LAYOUTS else "right",
        # Compteur automatique : l'écran calcule lui-même le nombre de jours écoulés depuis cette date
        "counter_date": r["counter_date"] if r["type"] == "chiffre" else "",
        # Anniversaires : seules les personnes fêtées aujourd'hui ou demain sont transmises aux écrans
        # (l'écran retient celles du jour selon sa propre horloge ; la liste complète reste sur le serveur)
        "birthdays": _birthdays_window(r) if r["type"] == "anniversaire" else [],
        # Vidéo sans durée imposée : lue jusqu'au bout (duration = 0)
        "duration": r["duration"] or (0 if r["type"] == "video" else default_duration),
    }


def save_upload(file_storage) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        flash(f"Format de fichier non supporté (.{ext}). Images : PNG, JPG, WebP, GIF, SVG – Vidéos : MP4, WebM.", "error")
        return None
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(UPLOAD_DIR / name)
    return name


def delete_upload(name: str):
    if name:
        p = (UPLOAD_DIR / name)
        if p.parent == UPLOAD_DIR and p.exists():
            try:
                p.unlink()
            except OSError:
                app.logger.warning("Impossible de supprimer %s (fichier en cours d'utilisation)", p)


def release_upload(db, name: str):
    """Supprime un fichier téléversé s'il n'est plus utilisé par aucune diapositive
    (une copie de diapositive partage l'image ou la vidéo de l'original)."""
    if name and not db.execute("SELECT 1 FROM slides WHERE image=? LIMIT 1", (name,)).fetchone():
        delete_upload(name)


# ---------------------------------------------------------------- public API
@app.route("/")
def index():
    return redirect(url_for("admin"))


@app.route("/display")
def display():
    return render_template("display.html")


def normalize_screen(name: str) -> str:
    return (name or "").strip().lower()[:40]


def screen_theme(screen: str, settings: dict, register: bool = True) -> str:
    """Thème de l'écran (réglage propre à l'écran, sinon thème par défaut).
    Enregistre aussi l'écran et l'heure de son dernier contact."""
    default = settings.get("default_theme") if settings.get("default_theme") in THEMES else "dark"
    name = normalize_screen(screen)
    if not name:
        return default
    db = get_db()
    if register:
        db.execute("INSERT INTO screens(name, last_seen) VALUES (?, ?) "
                   "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
                   (name, datetime.now().isoformat(timespec="seconds")))
        db.commit()
    row = db.execute("SELECT theme FROM screens WHERE name=?", (name,)).fetchone()
    return row["theme"] if row and row["theme"] in THEMES else default


def list_screens() -> list[dict]:
    """Écrans déjà connectés + écrans cités dans le ciblage des diapositives."""
    db = get_db()
    screens = {r["name"]: dict(r) for r in db.execute("SELECT * FROM screens")}
    for r in db.execute("SELECT screens FROM slides WHERE screens != ''"):
        for n in r["screens"].split(","):
            n = normalize_screen(n)
            if n and n not in screens:
                screens[n] = {"name": n, "theme": "", "last_seen": ""}
    now = datetime.now()
    for s in screens.values():
        s["online"] = bool(s["last_seen"]) and (now - datetime.fromisoformat(s["last_seen"])).total_seconds() < 180
    return sorted(screens.values(), key=lambda s: s["name"])


@app.route("/api/playlist")
def playlist():
    screen = request.args.get("screen", "").strip()
    preview = request.args.get("preview", type=int)
    settings = get_settings()
    default_duration = int(settings.get("default_duration") or 12)
    today = date.today().isoformat()
    rows = get_db().execute("SELECT * FROM slides ORDER BY position, id").fetchall()
    if preview and current_user():
        rows = [r for r in rows if r["id"] == preview]
    else:
        rows = [r for r in rows if is_live(r, today) and targets_screen(r, screen)]
    entities = entity_list(settings)
    slides = [slide_to_dict(r, default_duration, entities) for r in rows]
    if preview and current_user():
        # Aperçu d'une diapositive Anniversaires sans anniversaire aujourd'hui : on montre les prochains
        for s, r in zip(slides, rows):
            if s["type"] == "anniversaire" and not s["birthdays"]:
                nxt = next_birthday(load_people(r["people"]), date.today())
                if nxt:
                    s["birthdays"] = [_public_person(p) for p in nxt[1]]
                    s["preview_any_day"] = True
    else:
        # Pas d'anniversaire aujourd'hui ni demain : la diapositive n'est pas envoyée aux écrans
        slides = [s for s in slides if s["type"] != "anniversaire" or s["birthdays"]]
    ticker = [line.strip() for line in settings.get("ticker", "").splitlines() if line.strip()]
    payload = {
        "slides": slides,
        "settings": {
            "ticker": ticker if settings.get("show_ticker") == "1" else [],
            "show_clock": settings.get("show_clock") == "1",
            "theme": screen_theme(screen, settings, register=not preview),
        },
    }
    payload["version"] = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/install-ecran.sh")
def install_screen_script():
    """Script d'installation d'un écran, téléchargeable par les Raspberry Pi :
    curl -fsSL http://SERVEUR:8080/install-ecran.sh | bash -s -- --server SERVEUR --screen NOM"""
    path = BASE_DIR.parent / "deploy" / "install-ecran.sh"
    if not path.is_file():
        abort(404)
    resp = app.response_class(path.read_bytes(), mimetype="text/x-shellscript")
    resp.headers["Content-Disposition"] = 'inline; filename="install-ecran.sh"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/uploads/<path:name>")
def uploaded(name):
    resp = send_from_directory(UPLOAD_DIR, name, max_age=86400)
    # Empêche l'exécution de scripts éventuellement contenus dans un SVG téléversé
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


# --------------------------------------------------------------------- admin
@app.route("/admin")
@login_required
def admin():
    rows = get_db().execute("SELECT * FROM slides ORDER BY position, id").fetchall()
    today = date.today().isoformat()
    ents = entity_list()
    slides = [dict(r) | {"live": is_live(r, today), "media_type": media_type(r["image"]),
                         "counter_days": days_since(r["counter_date"]) if r["type"] == "chiffre" else None,
                         "entity_tags": slide_entities(r, ents),
                         "bday": _birthday_summary(r) if r["type"] == "anniversaire" else None}
              for r in rows]
    settings = get_settings()
    now = datetime.now()
    for s, r in zip(slides, rows):
        if s["bday"] is not None and not s["bday"]["today"]:   # diffusée seulement les jours d'anniversaire
            s["waiting"] = s["live"]
            s["live"] = False
        s["mail"] = mail_badge(r, settings, now)
    return render_template("admin.html", slides=slides, types=SLIDE_TYPES)


@app.route("/admin/administration")
@admin_required
def administration():
    """Onglet « Administration » : affichage, écrans, e-mail, utilisateurs, sauvegarde, mise à jour
    (un sous-onglet par section, choisi par l'adresse : …/administration#mail)."""
    settings = get_settings()
    return render_template("administration.html", settings=public_settings(settings),
                           screens=list_screens(), themes=THEMES, theme_icons=THEME_ICONS,
                           version=installed_version()[:7] or "inconnue", update_status=update_progress(),
                           can_update=UPDATER_BIN.exists(), backup=backup_info(), mail=mail_admin_info(settings),
                           users=list_users(settings), roles=ROLES, role_help=ROLE_HELP, password_min=PASSWORD_MIN,
                           master_min=MASTER_MIN)


def admin_tab(tab: str, query: str = ""):
    """Retour à une section de l'onglet Administration après un enregistrement."""
    return redirect(url_for("administration") + query + "#" + tab)


# ------------------------------------------------------------ mise à jour depuis l'admin
# La vérification interroge GitHub ; la mise à jour elle-même est faite par le système
# (comaxians-update, lancé en root par systemd) : le serveur web ne fait que déposer une demande.
GITHUB_REPO = os.environ.get("COMAXIANS_GITHUB_REPO", "garg24/comaxians")
GITHUB_BRANCH = "main"
UPDATER_BIN = Path(os.environ.get("COMAXIANS_UPDATER", "/usr/local/bin/comaxians-update"))
UPDATE_REQUEST = DATA_DIR / "update-request"
UPDATE_STATUS = DATA_DIR / "update-status.json"
UPDATE_LOG = DATA_DIR / "update.log"
_update_cache: dict = {}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def installed_version() -> str:
    """Commit installé : fichier VERSION écrit par install-serveur.sh, sinon dépôt git local."""
    f = BASE_DIR.parent / "VERSION"
    try:
        if f.exists():
            v = f.read_text(encoding="utf-8").strip()
            return v if v != "inconnue" else ""
        if (BASE_DIR.parent / ".git").exists():
            r = subprocess.run(["git", "-C", str(BASE_DIR.parent), "rev-parse", "HEAD"],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _github(path: str) -> dict:
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/{path}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "comaxians"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def _commit_info(c: dict) -> dict:
    return {"sha": c["sha"][:7], "message": c["commit"]["message"].split("\n", 1)[0][:140],
            "date": c["commit"]["committer"]["date"][:10]}


def check_update() -> dict:
    """Compare la version installée à la dernière version publiée sur GitHub (résultat gardé 5 min :
    l'API GitHub limite le nombre de requêtes sans compte)."""
    current = installed_version()
    cached = _update_cache.get(current)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    res = {"current": current[:7] or "inconnue", "available": False, "count": 0, "changes": [], "latest": None}
    cmp = None
    if len(current) == 40:
        try:
            cmp = _github(f"compare/{current}...{GITHUB_BRANCH}")
        except urllib.error.HTTPError as e:
            if e.code != 404:    # 404 : version locale inconnue de GitHub → on compare au dernier commit
                raise
    if cmp is not None:
        commits = [_commit_info(c) for c in cmp.get("commits", [])][::-1]   # plus récentes d'abord
        res |= {"available": cmp.get("ahead_by", 0) > 0, "count": cmp.get("ahead_by", 0),
                "changes": commits[:10], "latest": commits[0] if commits else {"sha": current[:7]}}
    else:
        last = _commit_info(_github(f"commits/{GITHUB_BRANCH}"))
        res |= {"latest": last, "available": not current.startswith(last["sha"]),
                "count": None, "changes": [last]}
    _update_cache.clear()
    _update_cache[current] = (time.time(), res)
    return res


def update_status() -> dict | None:
    try:
        return json.loads(UPDATE_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _log_tail(lines: int = 12) -> list[str]:
    """Dernières lignes du journal de mise à jour, sans les codes couleur du terminal."""
    try:
        with UPDATE_LOG.open("rb") as f:
            start = max(0, f.seek(0, os.SEEK_END) - 6000)
            f.seek(start)
            raws = f.read().decode("utf-8", "replace").split("\n")[1 if start else 0:]   # 1re ligne tronquée
    except OSError:
        return []
    out = []
    for raw in raws:
        # \r au milieu d'une ligne : progression réécrite sur place (apt…) → on garde l'état final
        line = _ANSI.sub("", raw.rstrip("\r").rsplit("\r", 1)[-1]).rstrip()
        if line.strip():
            out.append(line[:220])
    return out[-lines:]


def update_progress() -> dict:
    """État de la mise à jour pour l'admin : étape, pourcentage, durées (horloge du serveur), journal."""
    st = update_status() or {}
    if not st.get("state"):
        return {}
    now = datetime.now()
    for key, out in (("started", "elapsed"), ("time", "step_elapsed")):
        try:
            st[out] = max(0, int((now - datetime.fromisoformat(st[key])).total_seconds()))
        except (KeyError, TypeError, ValueError):
            pass
    if st["state"] != "done":
        st["log"] = _log_tail()
    return st


def _write_status(state: str, message: str):
    # Remplacement (fichier temporaire + renommage) plutôt que réécriture : après une mise à jour,
    # le fichier appartient à root (écrit par comaxians-update) et le service ne peut pas le modifier,
    # mais il peut le remplacer car le dossier de données lui appartient.
    tmp = UPDATE_STATUS.with_name(UPDATE_STATUS.name + ".web")
    tmp.write_text(json.dumps({"state": state, "message": message,
                               "time": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    os.replace(tmp, UPDATE_STATUS)


@app.route("/admin/update/check")
@admin_required
def update_check():
    try:
        res = check_update()
    except (OSError, ValueError, KeyError) as e:
        app.logger.warning("Vérification de mise à jour impossible : %s", e)
        return jsonify({"error": "GitHub injoignable depuis le serveur (pas d'accès Internet ?)"}), 502
    return jsonify(res | {"can_update": UPDATER_BIN.exists()})


@app.route("/admin/update/status")
@admin_required
def update_state():
    return jsonify(update_progress())


@app.route("/admin/update/start", methods=["POST"])
@admin_required
def update_start():
    if not UPDATER_BIN.exists():
        flash("Mise à jour automatique indisponible sur ce serveur : relancez la commande d'installation "
              "sur le Raspberry Pi (voir le README).", "error")
        return admin_tab("maj")
    st = update_status()
    recent = st and st.get("time", "") > (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    if st and st.get("state") in ("pending", "running") and recent:
        flash("Une mise à jour est déjà en cours.", "error")
        return admin_tab("maj")
    try:
        _write_status("pending", "Mise à jour demandée, démarrage…")
        UPDATE_REQUEST.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError as e:
        app.logger.error("Lancement de la mise à jour impossible : %s", e)
        flash(f"Impossible de lancer la mise à jour ({e.strerror or e}). Sur le Raspberry Pi, relancez la commande "
              "d'installation (voir le README).", "error")
        return admin_tab("maj")
    _update_cache.clear()
    flash("Mise à jour lancée : le serveur redémarre dans quelques minutes, vos contenus sont conservés.", "ok")
    return admin_tab("maj")


@app.route("/admin/screens", methods=["POST"])
@admin_required
def screen_save():
    name = normalize_screen(request.form.get("name", ""))
    theme = request.form.get("theme", "")
    if theme not in THEMES:
        theme = ""  # "" = suit le thème par défaut
    if not name:
        flash("Indiquez le nom de l'écran.", "error")
        return admin_tab("ecrans")
    db = get_db()
    db.execute("INSERT INTO screens(name, theme) VALUES (?, ?) "
               "ON CONFLICT(name) DO UPDATE SET theme=excluded.theme", (name, theme))
    db.commit()
    flash(f"Écran « {name} » enregistré. Le changement s'applique dans la minute.", "ok")
    return admin_tab("ecrans")


@app.route("/admin/screens/delete", methods=["POST"])
@admin_required
def screen_delete():
    get_db().execute("DELETE FROM screens WHERE name=?", (normalize_screen(request.form.get("name", "")),))
    get_db().commit()
    return admin_tab("ecrans")


def _form_values() -> dict:
    f = request.form
    t = f.get("type", "actualite")
    if t not in SLIDE_TYPES:
        t = "actualite"
    try:
        duration = max(0, min(900, int(f.get("duration") or 0)))
    except ValueError:
        duration = 0
    return {
        "type": t,
        "kicker": f.get("kicker", "").strip()[:80],
        "title": f.get("title", "").strip()[:200],
        "body": f.get("body", "").strip()[:600],
        "highlight": f.get("highlight", "").strip()[:20],
        "highlight_label": f.get("highlight_label", "").strip()[:60],
        "duration": duration,
        "start_date": f.get("start_date", "").strip(),
        "end_date": f.get("end_date", "").strip(),
        "screens": f.get("screens", "").strip(),
        "active": 1 if f.get("active") else 0,
        # Anniversaires : jamais envoyés par e-mail (contenu quotidien)
        "mail_on_publish": 1 if f.get("mail_on_publish") and t != "anniversaire" else 0,
        "sound": 1 if f.get("sound") else 0,
        "layout": f.get("layout") if f.get("layout") in LAYOUTS else "right",
        # Mode compteur ("days") : date du dernier événement ; sinon valeur fixe
        "counter_date": (_parse_date(f.get("counter_date", ""))
                         if t == "chiffre" and f.get("counter_mode") == "days" else ""),
        "_counter_requested": t == "chiffre" and f.get("counter_mode") == "days",
        # Entités cochées (seules celles déclarées dans les paramètres sont acceptées)
        "entities": ",".join(e["name"] for e in entity_list()
                             if e["name"].lower() in {x.strip().lower() for x in f.getlist("entities")}),
        **_people_values(t),
    }


# ------------------------------------------------------------- anniversaires
def _parse_day_month(value: str) -> tuple[int, int] | None:
    """« 14/03 », « 14/3 », « 14/03/1990 », « 14-03 » ou « 14.03 » → (14, 3). L'année est ignorée."""
    parts = [p for p in value.strip().replace("-", "/").replace(".", "/").split("/") if p]
    if len(parts) < 2 or not all(p.isdigit() for p in parts[:2]):
        return None
    day, month = int(parts[0]), int(parts[1])
    try:
        date(2000, month, day)          # 2000 est bissextile : le 29/02 est accepté
    except ValueError:
        return None
    return day, month


def _people_values(slide_type: str) -> dict:
    """Lit la liste des personnes du formulaire (lignes prénom / nom / date)."""
    if slide_type != "anniversaire":
        return {"people": ""}
    f = request.form
    rows = list(zip(f.getlist("people_first"), f.getlist("people_last"), f.getlist("people_date")))
    people, bad = [], []
    for i, (first, last, raw) in enumerate(rows, start=1):
        first, last, raw = first.strip()[:40], last.strip()[:40], raw.strip()
        if not (first or last or raw):
            continue                      # ligne vide : ignorée
        dm = _parse_day_month(raw)
        if not (first or last) or not dm:
            bad.append(f"ligne {i}" + (f" ({first} {last})".rstrip() if first or last else ""))
            continue
        people.append({"first": first, "last": last, "day": dm[0], "month": dm[1]})
    people.sort(key=lambda p: (p["month"], p["day"], p["last"].lower(), p["first"].lower()))
    out = {"people": json.dumps(people[:500], ensure_ascii=False),
           "_people_raw": [{"first": a, "last": b, "date": c} for a, b, c in rows]}
    if bad:
        out["_people_error"] = ("Date ou nom manquant / invalide : " + ", ".join(bad[:5])
                                + (" …" if len(bad) > 5 else "") + ". Format attendu de la date : JJ/MM.")
    return out


def birthday_on(p: dict, d: date) -> bool:
    """Vrai si l'anniversaire tombe le jour d. Les personnes nées un 29/02 sont fêtées le 28/02
    les années non bissextiles."""
    if p["month"] == 2 and p["day"] == 29 and not calendar.isleap(d.year):
        return d.month == 2 and d.day == 28
    return p["month"] == d.month and p["day"] == d.day


def load_people(raw: str) -> list[dict]:
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def _public_person(p: dict) -> dict:
    return {"first": p["first"], "last": p["last"], "day": p["day"], "month": p["month"]}


def _birthday_summary(r) -> dict:
    """Résumé pour la liste de l'admin : fêtés aujourd'hui, prochain anniversaire, effectif."""
    people = load_people(r["people"])
    today = date.today()
    nxt = next_birthday(people, today + timedelta(days=1))
    full = lambda p: f"{p['first']} {p['last']}".strip()

    def short(names: list[str], n: int = 4) -> list[str]:
        return names[:n] + ([f"+ {len(names) - n}"] if len(names) > n else [])

    return {
        "count": len(people),
        "today": short([full(p) for p in people if birthday_on(p, today)]),
        "next_date": (nxt[0].strftime("%d/%m") + (nxt[0].strftime("/%Y") if nxt[0].year != today.year else "")) if nxt else "",
        "next": short([full(p) for p in nxt[1]], 3) if nxt else [],
    }


def _birthdays_window(r) -> list[dict]:
    today = date.today()
    window = (today, today + timedelta(days=1))
    return [_public_person(p) for p in load_people(r["people"]) if any(birthday_on(p, d) for d in window)]


def next_birthday(people: list[dict], start: date) -> tuple[date, list[dict]] | None:
    """Prochaine date (à partir de start inclus) où au moins une personne est fêtée."""
    for i in range(0, 367):
        d = start + timedelta(days=i)
        who = [p for p in people if birthday_on(p, d)]
        if who:
            return d, who
    return None


def _parse_date(value: str) -> str:
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return ""


def days_since(iso: str) -> int | None:
    """Nombre de jours écoulés depuis la date (0 si la date est aujourd'hui ou dans le futur)."""
    if not iso:
        return None
    try:
        return max(0, (date.today() - date.fromisoformat(iso)).days)
    except ValueError:
        return None


def _db_values(v: dict) -> dict:
    """Retire les clés techniques (préfixe « _ ») avant écriture en base."""
    return {k: val for k, val in v.items() if not k.startswith("_")}


def _validate(v: dict, media: str) -> str | None:
    if v.get("_counter_requested") and not v["counter_date"]:
        return "Indiquez la date du dernier événement pour le compteur de jours."
    if v.get("_people_error"):
        return v["_people_error"]
    if v["type"] == "anniversaire" and not json.loads(v["people"] or "[]"):
        return "Ajoutez au moins une personne (prénom, nom et date d'anniversaire)."
    if not v["title"] and v["type"] not in TITLE_OPTIONAL:
        return "Le titre est obligatoire."
    if v["type"] == "video" and media_type(media) != "video":
        return "Une diapositive Vidéo nécessite un fichier vidéo (MP4 ou WebM)."
    if v["type"] == "image" and media_type(media) != "image":
        return "Une affiche plein écran nécessite une image."
    return None


@app.route("/admin/slides/new", methods=["GET", "POST"])
@login_required
def slide_new():
    if request.method == "POST":
        v = _form_values()
        v["image"] = save_upload(request.files.get("image")) or ""
        err = _validate(v, v["image"])
        if err:
            delete_upload(v["image"])
            flash(err, "error")
            return render_template("edit.html", slide=v | {"image": ""}, types=SLIDE_TYPES)
        db = get_db()
        v["position"] = (db.execute("SELECT COALESCE(MAX(position), -1) FROM slides").fetchone()[0]) + 1
        v["updated_at"] = datetime.now().isoformat(timespec="seconds")
        v = _db_values(v)
        cols = ", ".join(v)
        db.execute(f"INSERT INTO slides({cols}) VALUES ({', '.join('?' * len(v))})", list(v.values()))
        db.commit()
        flash("Diapositive créée.", "ok")
        return redirect(url_for("admin"))
    blank = {"type": request.args.get("type", "actualite"), "active": 1}
    # E-mail à la publication coché par défaut, sauf pour une vidéo (illisible dans un e-mail)
    blank["mail_on_publish"] = 0 if blank["type"] in ("video", "anniversaire") else 1
    if blank["type"] == "employe":   # pré-remplissage pratique
        blank |= {"kicker": "Employé du mois", "highlight": month_label(date.today())}
    if blank["type"] == "anniversaire":
        blank |= {"kicker": "Anniversaires", "title": "Joyeux anniversaire !",
                  "body": "Toute l'équipe vous souhaite une excellente journée !"}
    return render_template("edit.html", slide=blank, types=SLIDE_TYPES)


@app.route("/admin/slides/<int:sid>", methods=["GET", "POST"])
@login_required
def slide_edit(sid):
    db = get_db()
    row = db.execute("SELECT * FROM slides WHERE id=?", (sid,)).fetchone()
    if not row:
        abort(404)
    if request.method == "POST":
        v = _form_values()
        new_img = save_upload(request.files.get("image"))
        final = new_img or ("" if request.form.get("remove_image") else row["image"])
        err = _validate(v, final)
        if err:
            delete_upload(new_img or "")
            flash(err, "error")
            return render_template("edit.html", slide=dict(row) | v, types=SLIDE_TYPES)
        if final != row["image"]:
            v["image"] = final
        v["updated_at"] = datetime.now().isoformat(timespec="seconds")
        v = _db_values(v)
        sets = ", ".join(f"{k}=?" for k in v)
        db.execute(f"UPDATE slides SET {sets} WHERE id=?", [*v.values(), sid])
        db.commit()
        if final != row["image"]:
            release_upload(db, row["image"])   # ancien visuel, sauf s'il sert encore à une copie
        flash("Diapositive enregistrée.", "ok")
        return redirect(url_for("admin"))
    return render_template("edit.html", slide=dict(row), types=SLIDE_TYPES)


@app.route("/admin/slides/<int:sid>/delete", methods=["POST"])
@login_required
def slide_delete(sid):
    db = get_db()
    row = db.execute("SELECT image FROM slides WHERE id=?", (sid,)).fetchone()
    if row:
        db.execute("DELETE FROM slides WHERE id=?", (sid,))
        db.commit()
        release_upload(db, row["image"])   # le visuel reste s'il est partagé avec une copie
        flash("Diapositive supprimée.", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/slides/<int:sid>/duplicate", methods=["POST"])
@login_required
def slide_duplicate(sid):
    """Crée une copie de la diapositive, placée juste après l'original. La copie est désactivée
    (pour ne pas passer deux fois à l'écran) et s'ouvre directement en modification."""
    db = get_db()
    row = db.execute("SELECT * FROM slides WHERE id=?", (sid,)).fetchone()
    if not row:
        abort(404)
    v = {k: row[k] for k in row.keys() if k != "id"}
    if v["title"]:
        v["title"] = f"{v['title']} (copie)"
    v["active"] = 0
    v["mailed_at"] = v["mail_note"] = ""   # nouveau contenu : envoyé par e-mail à sa publication
    v["updated_at"] = datetime.now().isoformat(timespec="seconds")
    # Place libérée juste après l'original : les diapositives suivantes descendent d'un cran
    ids = [r["id"] for r in db.execute("SELECT id FROM slides ORDER BY position, id")]
    at = ids.index(sid)
    for pos, x in enumerate(ids):
        db.execute("UPDATE slides SET position=? WHERE id=?", (pos if pos <= at else pos + 1, x))
    v["position"] = at + 1
    cur = db.execute(f"INSERT INTO slides({', '.join(v)}) VALUES ({', '.join('?' * len(v))})", list(v.values()))
    db.commit()
    flash("Copie créée, désactivée pour l'instant : modifiez-la, cochez « Diapositive active », puis enregistrez.", "ok")
    return redirect(url_for("slide_edit", sid=cur.lastrowid))


@app.route("/admin/slides/<int:sid>/reset-counter", methods=["POST"])
@login_required
def slide_reset_counter(sid):
    """Remet le compteur de jours à zéro (nouvel événement aujourd'hui)."""
    db = get_db()
    db.execute("UPDATE slides SET counter_date=?, updated_at=? WHERE id=? AND type='chiffre' AND counter_date != ''",
               (date.today().isoformat(), datetime.now().isoformat(timespec="seconds"), sid))
    db.commit()
    flash("Compteur remis à zéro (date du dernier événement : aujourd'hui).", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/slides/<int:sid>/toggle", methods=["POST"])
@login_required
def slide_toggle(sid):
    db = get_db()
    # updated_at : l'e-mail d'un contenu activé part quelques minutes après (cf. mail_due)
    db.execute("UPDATE slides SET active = 1 - active, updated_at=? WHERE id=?",
               (datetime.now().isoformat(timespec="seconds"), sid))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/slides/<int:sid>/move/<direction>", methods=["POST"])
@login_required
def slide_move(sid, direction):
    """Déplace un contenu d'un cran (up / down) ou tout en haut / tout en bas (first / last)."""
    db = get_db()
    ids = [r["id"] for r in db.execute("SELECT id FROM slides ORDER BY position, id")]
    if sid in ids:
        i = ids.index(sid)
        j = {"up": i - 1, "down": i + 1, "first": 0, "last": len(ids) - 1}.get(direction, i)
        if 0 <= j < len(ids) and j != i:
            ids.insert(j, ids.pop(i))
            for pos, x in enumerate(ids):
                db.execute("UPDATE slides SET position=? WHERE id=?", (pos, x))
            db.commit()
    # La liste se rouvre sur le contenu déplacé (cf. admin.html), pas en haut de la page
    return redirect(url_for("admin") + f"#moved-{sid}")


@app.route("/admin/settings", methods=["POST"])
@admin_required
def settings_save():
    f = request.form
    try:
        duration = max(4, min(120, int(f.get("default_duration") or 12)))
    except ValueError:
        duration = 12
    values = {
        "ticker": f.get("ticker", "").strip()[:2000],
        "default_duration": str(duration),
        "show_clock": "1" if f.get("show_clock") else "0",
        "show_ticker": "1" if f.get("show_ticker") else "0",
        "default_theme": f.get("default_theme") if f.get("default_theme") in THEMES else "dark",
    }
    if "entities" in f:  # ne pas effacer la liste si le champ n'est pas envoyé
        values["entities"] = "\n".join(line.strip()[:30] for line in f["entities"].splitlines() if line.strip())[:400]
    db = get_db()
    for k, v in values.items():
        db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    db.commit()
    flash("Paramètres enregistrés.", "ok")
    return admin_tab("parametres")


# ------------------------------------------------------------------ utilisateurs
def _initials(text: str) -> str:
    parts = [p for p in re.split(r"[\s._@-]+", text) if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def list_users(settings: dict) -> list[dict]:
    me = current_user()
    out = []
    for r in get_db().execute("SELECT * FROM users ORDER BY builtin DESC, role, name COLLATE NOCASE, username"):
        u = {k: r[k] for k in r.keys() if k not in ("password", "token")}
        u["me"] = bool(me and me["id"] == r["id"])
        u["initials"] = _initials(r["name"] or r["username"])
        u["last_login_text"] = _fmt_stamp(r["last_login"]) if r["last_login"] else ""
        # Compte d'origine encore avec le mot de passe « axians » par défaut (aucun calcul de hachage :
        # le compte a gardé le mot de passe de l'installation, et celui-ci est celui par défaut)
        u["default_password"] = bool(r["builtin"] and ADMIN_PASSWORD == "axians"
                                     and r["password"] == settings.get("admin_env_hash"))
        out.append(u)
    return out


def _user_back(uid: int | None = None):
    return admin_tab("utilisateurs", f"?user={uid}" if uid else "")


def _rotate_token(db, uid: int) -> str:
    """Nouveau jeton : les sessions ouvertes avec l'ancien mot de passe sont fermées (sauf la mienne)."""
    tok = secrets.token_hex(16)
    db.execute("UPDATE users SET token=? WHERE id=?", (tok, uid))
    me = current_user()
    if me and me["id"] == uid:
        session["tok"] = tok
    return tok


@app.route("/admin/users", methods=["POST"])
@admin_required
def user_create():
    f = request.form
    username = f.get("username", "").strip().lower()
    name = " ".join(f.get("name", "").split())[:60]
    role = f.get("role") if f.get("role") in ROLES else "editor"
    password = f.get("password", "")
    db = get_db()
    if not _USERNAME.match(username):
        flash("Identifiant invalide : 2 à 40 caractères, lettres sans accent, chiffres, point, tiret, @ "
              "(ex. : prenom.nom).", "error")
    elif db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
        flash(f"L'identifiant « {username} » est déjà utilisé.", "error")
    elif err := password_error(password):
        flash(err, "error")
    else:
        db.execute("INSERT INTO users(username, name, role, password, token, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                   (username, name, role, generate_password_hash(password), secrets.token_hex(16),
                    datetime.now().isoformat(timespec="seconds")))
        db.commit()
        flash(f"Compte « {name or username} » créé ({ROLES[role].lower()}). Communiquez-lui son identifiant "
              f"« {username} » et son mot de passe : il pourra le changer dans « Mon compte ».", "ok")
    return _user_back()


@app.route("/admin/users/<int:uid>", methods=["POST"])
@admin_required
def user_save(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        abort(404)
    f = request.form
    name = " ".join(f.get("name", "").split())[:60]
    role = f.get("role", u["role"])
    if role not in ROLES:
        role = u["role"]
    me = current_user()
    if role != u["role"] and (u["builtin"] or u["id"] == me["id"]):
        flash("Le rôle du compte d'origine et celui de votre propre compte ne peuvent pas être changés.", "error")
        return _user_back(uid)
    if role != "admin" and u["role"] == "admin" and \
            db.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] <= 1:
        flash("Il faut garder au moins un administrateur.", "error")
        return _user_back(uid)
    password = f.get("password", "")
    if password and (err := password_error(password)):
        flash(err, "error")
        return _user_back(uid)
    db.execute("UPDATE users SET name=?, role=? WHERE id=?", (name, role, uid))
    if password:
        db.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(password), uid))
        _rotate_token(db, uid)
    db.commit()
    label = name or u["username"]
    flash(f"Compte « {label} » enregistré" + (" ; nouveau mot de passe actif." if password else "."), "ok")
    return _user_back()


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    db = get_db()
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return _user_back()
    if u["builtin"] or u["id"] == current_user()["id"]:
        flash("Le compte d'origine et votre propre compte ne peuvent pas être supprimés.", "error")
        return _user_back(uid)
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()
    flash(f"Compte « {u['name'] or u['username']} » supprimé.", "ok")
    return _user_back()


@app.route("/compte", methods=["GET", "POST"])
@login_required
def account():
    """Mon compte : nom affiché et mot de passe (le mot de passe actuel est demandé pour le changer)."""
    db = get_db()
    me = current_user()
    if request.method == "POST":
        f = request.form
        name = " ".join(f.get("name", "").split())[:60]
        new = f.get("new_password", "")
        if new:
            if not check_password(me, f.get("current_password", "")):
                flash("Mot de passe actuel incorrect.", "error")
                return redirect(url_for("account"))
            if err := password_error(new, f.get("confirm_password", "")):
                flash(err, "error")
                return redirect(url_for("account"))
            db.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(new), me["id"]))
            _rotate_token(db, me["id"])
        db.execute("UPDATE users SET name=? WHERE id=?", (name, me["id"]))
        db.commit()
        flash("Mot de passe changé : vos autres sessions ouvertes sont fermées." if new else "Compte enregistré.", "ok")
        return redirect(url_for("account"))
    return render_template("account.html", me=me, roles=ROLES, role_help=ROLE_HELP, password_min=PASSWORD_MIN)


# ------------------------------------------------------------ e-mail à la publication
# Chaque contenu est envoyé une seule fois par e-mail, avec sa mise en forme, quand il commence à
# être diffusé (enregistrement, activation ou date de début atteinte) : aux adresses de ses entités,
# ou à la liste « contenus sans entité ». La tâche de fond lancée par run.py (start_mailer)
# regarde toutes les 30 secondes s'il y a un contenu à envoyer.
MAIL_TICK = 30          # secondes entre deux vérifications
MAIL_DELAY = 120        # délai après l'enregistrement, le temps de corriger une coquille
MAIL_HOUR = 8           # une publication programmée (date de début) part à partir de 8 h ce jour-là
MAIL_SECURITY = {"starttls": ("STARTTLS", 587), "ssl": ("SSL/TLS", 465), "none": ("Aucune (relais interne)", 25)}
MAIL_THEMES = {"dark": "Sombre", "light": "Clair"}
_EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
                    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$")
TYPE_ACCENTS = {   # mêmes couleurs que sur les écrans (display.css)
    "succes": ("#FF3D9A", "#8B5CF6"), "actualite": ("#2F6BFF", "#22D3EE"),
    "partenaire": ("#22D3EE", "#2F6BFF"), "evenement": ("#F59E0B", "#FF3D9A"),
    "chiffre": ("#8B5CF6", "#FF3D9A"), "message": ("#2F6BFF", "#E0218A"),
    "video": ("#FF5A5F", "#F59E0B"),
}
DEFAULT_ACCENT = ("#2F6BFF", "#E0218A")
DAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_mail_backoff = {"until": None, "fails": 0}   # après un échec, nouvel essai de plus en plus espacé
_mail_thread: threading.Thread | None = None


def public_settings(settings: dict) -> dict:
    """Paramètres transmis aux pages de l'admin : jamais le mot de passe SMTP ni celui de l'installation."""
    return {k: v for k, v in settings.items() if k not in SECRET_SETTINGS}


def smtp_password(settings: dict) -> str:
    return os.environ.get("COMAXIANS_SMTP_PASSWORD") or settings.get("smtp_password") or ""


def smtp_port(settings: dict) -> int:
    sec = settings.get("smtp_security") if settings.get("smtp_security") in MAIL_SECURITY else "starttls"
    try:
        port = int(settings.get("smtp_port") or 0)
    except ValueError:
        port = 0
    return port if 0 < port < 65536 else MAIL_SECURITY[sec][1]


def mail_config_error(settings: dict) -> str | None:
    """Ce qui manque pour pouvoir envoyer, ou None."""
    if not settings.get("smtp_host"):
        return "indiquez le serveur d'envoi (SMTP)"
    if not _EMAIL.match(settings.get("mail_from") or ""):
        return "indiquez l'adresse d'expédition"
    if settings.get("smtp_user") and not smtp_password(settings):
        return "indiquez le mot de passe du compte d'envoi"
    return None


def parse_addresses(raw: str) -> tuple[list[str], list[str]]:
    """Adresses d'un champ libre (virgules, points-virgules, espaces ou une par ligne). Une liste copiée
    depuis Outlook (« Dupont, Jean <jean.dupont@axians.fr>; … ») convient : les noms sont ignorés.
    Renvoie (adresses valides, sans doublon ; éléments refusés)."""
    ok, bad, seen = [], [], set()
    for token in re.split(r"[\s,;<>()\"\[\]]+", raw or ""):
        token = token.strip(".'")
        if "@" not in token:
            continue
        if _EMAIL.match(token) and len(token) <= 254:
            if token.lower() not in seen:
                seen.add(token.lower())
                ok.append(token)
        else:
            bad.append(token)
    return ok[:500], bad


def mail_lists(settings: dict) -> dict[str, list[str]]:
    """Destinataires par entité (nom en minuscules ; "" = contenus sans entité)."""
    try:
        raw = json.loads(settings.get("mail_recipients") or "{}")
    except ValueError:
        raw = {}
    lists = {}
    for name, addrs in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(name, str) and isinstance(addrs, list):
            lists[name.strip().lower()] = [a for a in addrs if isinstance(a, str) and _EMAIL.match(a)]
    return lists


def slide_recipients(r, settings: dict, ents: list[dict] | None = None) -> tuple[list[str], list[str]]:
    """(destinataires sans doublon, entités du contenu) : réunion des listes de ses entités."""
    lists = mail_lists(settings)
    names = [e["name"] for e in slide_entities(r, entity_list(settings) if ents is None else ents)]
    out, seen = [], set()
    for key in [n.lower() for n in names] or [""]:
        for a in lists.get(key, []):
            if a.lower() not in seen:
                seen.add(a.lower())
                out.append(a)
    return out, names


def _mix(a: str, b: str, pct: float) -> str:
    """Couleur a posée à pct % sur la couleur b : équivalent opaque de color-mix(), lu par Outlook."""
    x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(p * pct / 100 + q * (1 - pct / 100)):02X}" for p, q in zip(x, y))


def mail_palette(theme: str, accent: str, accent2: str) -> dict:
    dark = theme != "light"
    page, card, text = ("#070B18", "#0D1428", "#F4F6FB") if dark else ("#F3F5FA", "#FFFFFF", "#0B1222")
    return {
        "scheme": "dark" if dark else "light", "page": page, "card": card, "text": text,
        "dim": _mix(text, card, 70 if dark else 64),
        "line": _mix(text, card, 14 if dark else 10),
        "glass": _mix(text, card, 6 if dark else 4),
        "accent": accent, "accent2": accent2,
        "accent_text": accent if dark else _mix(accent, "#000000", 80),   # lisible sur fond blanc
        "tag_bg": _mix(accent, card, 16 if dark else 10),
        "tag_line": _mix(accent, card, 45 if dark else 35),
        "contrast": text, "contrast_text": card,
        "logo": "axians-logo-mail-white.png" if dark else "axians-logo-mail.png",
    }


def long_date(d: date) -> str:
    """« Lundi 23 novembre 2026 »"""
    return f"{DAYS_FR[d.weekday()].capitalize()} {'1er' if d.day == 1 else d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def _col(r, key: str, default=""):
    try:
        v = r[key]
    except (IndexError, KeyError):
        return default
    return default if v is None else v


def mail_view(r, settings: dict, ents: list[dict] | None = None) -> dict:
    """Ce qu'affiche l'e-mail d'un contenu (r : ligne de la base ou dictionnaire équivalent)."""
    t = _col(r, "type") or "actualite"
    image = _col(r, "image")
    theme = settings.get("mail_theme") if settings.get("mail_theme") in MAIL_THEMES else "dark"
    v = {
        "id": _col(r, "id", None), "type": t, "type_label": SLIDE_TYPES.get(t, t),
        "kicker": _col(r, "kicker").strip(), "title": _col(r, "title").strip(), "body": _col(r, "body").strip(),
        "highlight": _col(r, "highlight").strip(), "highlight_label": _col(r, "highlight_label").strip(),
        "entities": slide_entities(r, entity_list(settings) if ents is None else ents),
        "image": image if media_type(image) == "image" else "",
        "has_video": media_type(image) == "video",
        "site_name": settings.get("site_name") or "Axians",
        "date_label": long_date(date.today()),
        "c": mail_palette(theme, *TYPE_ACCENTS.get(t, DEFAULT_ACCENT)),
    }
    v["body_html"] = Markup("<br>").join(escape(line) for line in v["body"].splitlines())
    v["initials"] = "".join(w[0] for w in v["title"].split()[:2]).upper()
    if t == "chiffre":   # compteur de jours : valeur du jour de l'envoi
        n = days_since(_col(r, "counter_date"))
        v["value"] = f"{n:,}".replace(",", " ") if n is not None else (v["highlight"] or "—")
    v["subject"] = mail_subject(v)
    return v


def mail_subject(v: dict) -> str:
    title, kicker = v["title"], v["kicker"]
    if kicker and title and kicker.lower() not in title.lower():
        s = f"{kicker} – {title}"
    else:
        s = title or kicker or v["type_label"]
    return re.sub(r"\s+", " ", s).strip()[:180]


def mail_text(v: dict) -> str:
    """Version texte de l'e-mail (lue par les messageries qui n'affichent pas le HTML)."""
    lines = []
    tags = ([v["kicker"]] if v["kicker"] else []) + [e["name"] for e in v["entities"]]
    if tags:
        lines += [" · ".join(tags), ""]
    if v["type"] == "chiffre":
        lines += [f"{v['value']} {v['highlight_label']}".strip(), ""]
    if v["type"] == "evenement" and v["highlight"]:
        lines += [f"{v['highlight']} {v['highlight_label']}".strip(), ""]
    if v["title"] and v["type"] != "image":
        lines.append(v["title"])
    if v["type"] == "employe":
        lines += [x for x in (v["highlight_label"], f"★ {v['highlight']}" if v["highlight"] else "") if x]
    if v["body"] and v["type"] != "image":
        lines += ["", v["body"]]
    if v["type"] in ("succes", "actualite") and v["highlight"]:
        lines += ["", f"{v['highlight']} – {v['highlight_label']}".strip(" –")]
    if v["type"] == "video":
        lines += ["", "(Vidéo à découvrir sur les écrans d'affichage)"]
    lines += ["", "--", f"Publié sur les écrans d'affichage {v['site_name']} – {v['date_label']}"]
    return "\n".join(lines).strip() + "\n"


# Images jointes : taille réelle maximale (affichées deux fois plus petites, nettes sur écran haute densité)
MAIL_IMG_MAX = {"hero": (1196, 1600), "poster": (1196, 2400), "logo": (520, 280), "portrait": (360, 360)}
MAIL_IMG_BOX = {"hero": (598, 800), "poster": (598, 1200), "logo": (260, 140), "portrait": (180, 180)}


def _mail_kind(v: dict) -> str | None:
    if not v["image"]:
        return None
    return {"partenaire": "logo", "employe": "portrait", "image": "poster"}.get(v["type"], "hero")


def _display_size(w: int, h: int, kind: str) -> dict:
    bw, bh = MAIL_IMG_BOX[kind]
    scale = bw / w if kind in ("hero", "poster") else min(bw / w, bh / h)
    return {"w": round(w * scale), "h": round(h * scale)}


def _portrait(im, c: dict) -> dict:
    """Photo ronde entourée d'un anneau dégradé, comme sur les écrans (PNG transparent)."""
    size, ring, gap, k = 360, 10, 12, 3            # k : suréchantillonnage pour des bords lisses
    big, lanczos = size * k, Image.Resampling.LANCZOS
    inner = big - 2 * (ring + gap) * k
    photo = ImageOps.fit(im.convert("RGB"), (inner, inner), method=lanczos, centering=(0.5, 0.4))
    grad = Image.linear_gradient("L").rotate(90).resize((big, big))   # de gauche à droite
    ring_img = Image.composite(Image.new("RGB", (big, big), c["accent2"]),
                               Image.new("RGB", (big, big), c["accent"]), grad)
    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    mask = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, big - 1, big - 1), fill=255)
    draw.ellipse((ring * k, ring * k, big - 1 - ring * k, big - 1 - ring * k), fill=0)
    out.paste(ring_img, (0, 0), mask)
    mask = Image.new("L", (inner, inner), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, inner - 1, inner - 1), fill=255)
    out.paste(photo, ((ring + gap) * k,) * 2, mask)
    buf = io.BytesIO()
    out.resize((size, size), lanczos).save(buf, "PNG", optimize=True)
    return {"data": buf.getvalue(), "subtype": "png", **_display_size(size, size, "portrait")}


def mail_image(name: str, kind: str, c: dict) -> dict | None:
    """Image jointe à l'e-mail : {data, subtype, w, h} (w, h : taille affichée), ou None si impossible."""
    path = UPLOAD_DIR / name
    ext = name.rsplit(".", 1)[-1].lower()
    if not _media_ok(name) or not path.is_file() or ext == "svg":   # SVG : ni Outlook ni Gmail ne l'affichent
        return None
    if Image is None:   # sans Pillow : JPEG, PNG et GIF joints tels quels ; WebP illisible dans Outlook
        if ext not in ("jpg", "jpeg", "png", "gif") or path.stat().st_size > 8 * 1024 * 1024:
            return None
        return {"data": path.read_bytes(), "subtype": "jpeg" if ext in ("jpg", "jpeg") else ext, "w": None, "h": None}
    try:
        with Image.open(path) as src:
            if getattr(src, "is_animated", False) and ext == "gif" and path.stat().st_size <= 4 * 1024 * 1024:
                return {"data": path.read_bytes(), "subtype": "gif", **_display_size(*src.size, kind)}  # animation gardée
            im = ImageOps.exif_transpose(src)
            if kind == "portrait":
                return _portrait(im, c)
            im.thumbnail(MAIL_IMG_MAX[kind], Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
                im.convert("RGBA").save(buf, "PNG", optimize=True)   # logo détouré : transparence gardée
                subtype = "png"
            else:
                im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True, progressive=True)
                subtype = "jpeg"
            return {"data": buf.getvalue(), "subtype": subtype, **_display_size(*im.size, kind)}
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        app.logger.warning("Image %s non jointe à l'e-mail : %s", name, e)
        return None


def _mail_logo(c: dict) -> dict | None:
    p = BASE_DIR / "static" / "img" / c["logo"]
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", data[16:24])
    return {"data": data, "subtype": "png", "w": round(w * 26 / h), "h": 26}


def mail_assets(v: dict) -> dict:
    """Images de l'e-mail : logo et visuel du contenu."""
    assets = {}
    logo = _mail_logo(v["c"])
    if logo:
        assets["logo"] = logo
    kind = _mail_kind(v)
    img = mail_image(v["image"], kind, v["c"]) if kind else None
    if img:
        assets["media"] = img | {"kind": kind}
    return assets


def mail_warnings(v: dict, assets: dict) -> list[str]:
    """Ce qui, dans le contenu, ne peut pas figurer dans l'e-mail."""
    out = []
    if v["image"] and "media" not in assets:
        ext = v["image"].rsplit(".", 1)[-1].lower()
        out.append("Image SVG : ni Outlook ni Gmail ne l'affichent, elle n'est pas jointe à l'e-mail." if ext == "svg"
                   else "Image WebP : Outlook ne l'affiche pas et ce serveur ne peut pas la convertir (module "
                        "Pillow absent, cf. README) : elle n'est pas jointe à l'e-mail." if ext == "webp"
                   else "Image introuvable ou illisible : elle n'est pas jointe à l'e-mail.")
    if v["has_video"]:
        out.append("Une vidéo ne peut pas être lue dans un e-mail : il invite à la découvrir sur les écrans.")
    return out


def render_mail_html(v: dict, assets: dict, cids: dict | None = None) -> str:
    """HTML de l'e-mail. Avec cids : images jointes (envoi) ; sans : images intégrées (aperçu)."""
    img = {role: a | {"src": f"cid:{cids[role]}" if cids else
                      f"data:image/{a['subtype']};base64,{base64.b64encode(a['data']).decode()}"}
           for role, a in assets.items()}
    return render_template("mail/publication.html", v=v, c=v["c"], img=img, F=MAIL_FONT)


MAIL_FONT = "font-family:Inter,Segoe UI,Roboto,Helvetica Neue,Arial,sans-serif;"


def build_mail(v: dict, settings: dict, to: str | None = None, test: bool = False) -> EmailMessage:
    """E-mail prêt à partir (HTML + texte, images jointes). Sans « to » : adressé à l'expéditeur,
    les destinataires sont en copie cachée (cf. smtp_send)."""
    from_addr = settings["mail_from"]
    domain = from_addr.rsplit("@", 1)[-1]
    sender = formataddr((settings.get("mail_from_name") or f"{v['site_name']} Affichage", from_addr))
    assets = mail_assets(v)
    msg = EmailMessage()
    msg["Subject"] = ("[Test] " if test else "") + v["subject"]
    msg["From"] = sender
    msg["To"] = to or sender
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Auto-Submitted"] = "auto-generated"          # pas de réponse automatique (message d'absence…)
    msg["X-Auto-Response-Suppress"] = "All"
    msg.set_content(mail_text(v))
    cids = {role: make_msgid(domain=domain)[1:-1] for role in assets}
    msg.add_alternative(render_mail_html(v, assets, cids), subtype="html")
    html = msg.get_payload()[-1]
    for role, a in assets.items():
        html.add_related(a["data"], "image", a["subtype"], cid=f"<{cids[role]}>",
                         filename=f"{role}.{'jpg' if a['subtype'] == 'jpeg' else a['subtype']}")
    return msg


def smtp_send(settings: dict, msg: EmailMessage, recipients: list[str]) -> dict:
    """Envoie msg aux destinataires (enveloppe SMTP : en copie cachée). Renvoie les adresses refusées."""
    sec = settings.get("smtp_security") if settings.get("smtp_security") in MAIL_SECURITY else "starttls"
    host, port, ctx = settings["smtp_host"], smtp_port(settings), ssl.create_default_context()
    server = (smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) if sec == "ssl"
              else smtplib.SMTP(host, port, timeout=30))
    refused = {}
    with server as s:
        s.ehlo()
        if sec == "starttls":
            s.starttls(context=ctx)
            s.ehlo()
        if settings.get("smtp_user"):
            s.login(settings["smtp_user"], smtp_password(settings))
        for i in range(0, len(recipients), 100):   # limite de destinataires par message des serveurs
            refused.update(s.send_message(msg, from_addr=settings["mail_from"], to_addrs=recipients[i:i + 100]))
    return refused


def mail_error_text(e: Exception) -> str:
    """Message compréhensible pour l'admin, suivi du détail technique."""
    checks = [
        (smtplib.SMTPAuthenticationError, "identifiant ou mot de passe refusé par le serveur d'envoi"),
        (smtplib.SMTPNotSupportedError, "mode de sécurité non proposé par le serveur d'envoi (essayez un autre mode ou port)"),
        (smtplib.SMTPSenderRefused, "adresse d'expédition refusée par le serveur d'envoi"),
        (smtplib.SMTPRecipientsRefused, "toutes les adresses ont été refusées par le serveur d'envoi"),
        (smtplib.SMTPServerDisconnected, "connexion interrompue par le serveur d'envoi"),
        (smtplib.SMTPResponseException, "envoi refusé par le serveur d'envoi"),
        (ssl.SSLCertVerificationError, "certificat du serveur d'envoi non reconnu"),
        (ssl.SSLError, "connexion chiffrée impossible (vérifiez le mode de sécurité et le port)"),
        (socket.gaierror, "serveur d'envoi introuvable (nom incorrect ou pas d'accès au réseau)"),
        (ConnectionRefusedError, "connexion refusée (serveur ou port incorrect)"),
        (TimeoutError, "le serveur d'envoi ne répond pas (pare-feu, serveur ou port incorrect)"),
        (OSError, "connexion au serveur d'envoi impossible"),
    ]
    text = next((t for cls, t in checks if isinstance(e, cls)), "envoi impossible")
    if isinstance(e, smtplib.SMTPResponseException):
        err = e.smtp_error.decode(errors="replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        detail = f"{e.smtp_code} {err}"
    else:
        detail = str(e)
    detail = " ".join(detail.split())[:200]
    return f"{text} ({detail})" if detail else text


def _set_mail_status(db, ok: bool, message: str, now: datetime | None = None):
    status = {"time": (now or datetime.now()).isoformat(timespec="seconds"), "ok": ok, "message": message[:400]}
    db.execute("INSERT INTO settings(key, value) VALUES('mail_status', ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(status, ensure_ascii=False),))


def mail_due(r) -> datetime:
    """Heure d'envoi d'un contenu diffusé : MAIL_DELAY après son enregistrement, et pas avant
    MAIL_HOUR le jour de sa date de début."""
    due = datetime.min
    try:
        due = datetime.fromisoformat(r["updated_at"]) + timedelta(seconds=MAIL_DELAY)
    except (TypeError, ValueError):
        pass
    try:
        if r["start_date"]:
            due = max(due, datetime.combine(date.fromisoformat(r["start_date"]), dtime(MAIL_HOUR)))
    except ValueError:
        pass
    return due


def _slide_label(r) -> str:
    return _col(r, "title") or SLIDE_TYPES.get(_col(r, "type"), "Contenu")


def send_slide(r, settings: dict, recipients: list[str], ents=None, test: bool = False) -> dict:
    v = mail_view(r, settings, ents)
    return smtp_send(settings, build_mail(v, settings, to=recipients[0] if test else None, test=test), recipients)


def _sent_note(recipients: list[str], refused: dict) -> str:
    note = f"Envoyé à {_plural(len(recipients) - len(refused), 'destinataire', 'destinataires')}"
    if refused:
        note += f" ; refusé{'s' if len(refused) > 1 else ''} par le serveur : {', '.join(list(refused)[:5])}"
    return note


def mail_tick(now: datetime | None = None) -> list[int]:
    """Envoie les contenus dont la diffusion a commencé ; renvoie leurs numéros."""
    now = now or datetime.now()
    settings = get_settings()
    if settings.get("mail_enabled") != "1" or mail_config_error(settings):
        return []
    if _mail_backoff["until"] and now < _mail_backoff["until"]:
        return []
    db = get_db()
    today, ents, sent = now.date().isoformat(), entity_list(settings), []
    rows = db.execute("SELECT * FROM slides WHERE mailed_at='' AND mail_on_publish=1 AND type != 'anniversaire' "
                      "ORDER BY position, id").fetchall()
    for r in rows:
        if not is_live(r, today) or now < mail_due(r):
            continue
        stamp = now.isoformat(timespec="seconds")
        recipients, names = slide_recipients(r, settings, ents)
        if not recipients:
            note = "Non envoyé : aucune adresse pour " + (", ".join(names) if names else "les contenus sans entité")
            db.execute("UPDATE slides SET mailed_at=?, mail_note=? WHERE id=? AND mailed_at=''", (stamp, note, r["id"]))
            db.commit()
            continue
        # Réservation : un contenu ne part qu'une fois, même si deux envois se croisent
        if db.execute("UPDATE slides SET mailed_at=?, mail_note='Envoi en cours…' WHERE id=? AND mailed_at=''",
                      (stamp, r["id"])).rowcount != 1:
            continue
        db.commit()
        try:
            refused = send_slide(r, settings, recipients, ents)
        except Exception as e:   # contenu remis en attente : nouvel essai plus tard
            if not isinstance(e, (smtplib.SMTPException, OSError)):
                app.logger.exception("E-mail du contenu %s", r["id"])
            err = mail_error_text(e)
            db.execute("UPDATE slides SET mailed_at='', mail_note=? WHERE id=? AND mailed_at=?",
                       (f"Échec : {err}", r["id"], stamp))
            _set_mail_status(db, False, f"« {_slide_label(r)} » non envoyé : {err}", now)
            db.commit()
            _mail_backoff["fails"] += 1
            _mail_backoff["until"] = now + timedelta(seconds=min(1800, 60 * 2 ** (_mail_backoff["fails"] - 1)))
            app.logger.warning("E-mail du contenu %s non envoyé : %s", r["id"], err)
            break
        note = _sent_note(recipients, refused)
        db.execute("UPDATE slides SET mail_note=? WHERE id=?", (note, r["id"]))
        _set_mail_status(db, True, f"« {_slide_label(r)} » : {note[0].lower()}{note[1:]}", now)
        db.commit()
        _mail_backoff.update(until=None, fails=0)
        sent.append(r["id"])
    return sent


def _mail_loop():
    while True:
        time.sleep(MAIL_TICK)
        try:
            with app.app_context():
                mail_tick()
        except Exception:   # la tâche ne doit jamais s'arrêter
            app.logger.exception("Envoi des e-mails")


def start_mailer():
    """Lance la tâche d'envoi des e-mails (une seule fois ; appelé par run.py)."""
    global _mail_thread
    if _mail_thread is None:
        _mail_thread = threading.Thread(target=_mail_loop, name="comaxians-mail", daemon=True)
        _mail_thread.start()


def mark_live_as_handled(db, note: str, today: str | None = None) -> int:
    """Contenus déjà en diffusion : considérés comme traités, sans envoi (activation de l'envoi, import).
    Renvoie le nombre de ceux qui auraient été envoyés."""
    today = today or date.today().isoformat()
    stamp, n = datetime.now().isoformat(timespec="seconds"), 0
    for r in db.execute("SELECT * FROM slides WHERE mailed_at=''").fetchall():
        if is_live(r, today):
            db.execute("UPDATE slides SET mailed_at=?, mail_note=? WHERE id=?", (stamp, note, r["id"]))
            n += bool(r["mail_on_publish"]) and r["type"] != "anniversaire"
    return n


def _fmt_stamp(iso: str) -> str:
    return f"le {iso[8:10]}/{iso[5:7]} à {iso[11:16]}" if len(iso) >= 16 else ""


def mail_badge(r, settings: dict, now: datetime) -> dict | None:
    """Pastille « ✉ » de la liste des contenus (None : rien à signaler)."""
    if r["type"] == "anniversaire" or not r["mail_on_publish"]:
        return None
    note = r["mail_note"] or ""
    if r["mailed_at"]:
        if note.startswith("Envoyé"):
            return {"text": f"✉ Envoyé {_fmt_stamp(r['mailed_at'])}", "cls": "ok", "title": note}
        if note.startswith("Non envoyé : aucune adresse"):
            return {"text": "✉ Non envoyé : aucune adresse", "cls": "warn",
                    "title": note + " (Administration › E-mail)"}
        if note.startswith("Envoi en cours"):
            return {"text": "✉ Envoi en cours…", "cls": "", "title": ""}
        return None   # déjà en diffusion à l'activation de l'envoi, ou importé : pas d'envoi
    if settings.get("mail_enabled") != "1":
        return None
    today = now.date().isoformat()
    if is_live(r, today):
        if note.startswith("Échec"):
            return {"text": "✉ Échec de l'envoi · nouvel essai automatique", "cls": "err", "title": note}
        due = mail_due(r)
        return {"text": f"✉ Envoi prévu à {due:%H:%M}" if due > now else "✉ Envoi imminent", "cls": "",
                "title": "L'e-mail part quelques minutes après l'enregistrement : vous pouvez encore corriger le contenu."}
    if r["active"] and r["start_date"] and r["start_date"] > today and not (r["end_date"] and r["end_date"] < r["start_date"]):
        return {"text": f"✉ Envoi le {r['start_date'][8:10]}/{r['start_date'][5:7]} à {MAIL_HOUR} h", "cls": "",
                "title": "Envoyé le jour de sa date de début de diffusion."}
    return None


def mail_admin_info(settings: dict) -> dict:
    """Données de la section « E-mail à la publication »."""
    lists = mail_lists(settings)
    rows = [{"key": e["name"], "label": e["name"], "color": e["color"]} for e in entity_list(settings)]
    rows.append({"key": "", "label": "Contenus sans entité", "color": None})
    for row in rows:
        row["addrs"] = "\n".join(lists.get(row["key"].lower(), []))
        row["count"] = len(lists.get(row["key"].lower(), []))
    try:
        status = json.loads(settings.get("mail_status") or "null")
    except ValueError:
        status = None
    if isinstance(status, dict):
        status["when"] = _fmt_stamp(str(status.get("time", "")))
    return {"lists": rows, "status": status if isinstance(status, dict) else None,
            "password_set": bool(settings.get("smtp_password")),
            "password_env": bool(os.environ.get("COMAXIANS_SMTP_PASSWORD")),
            "port": smtp_port(settings), "security": MAIL_SECURITY, "themes": MAIL_THEMES,
            "config_error": mail_config_error(settings), "pillow": Image is not None,
            "test_to": settings.get("mail_test_to", ""), "delay": MAIL_DELAY // 60, "hour": MAIL_HOUR}


def mail_edit_hint(slide: dict) -> str:
    """Explication sous la case « Envoyer par e-mail à la publication » (page de modification)."""
    settings = get_settings()
    mailed, note = slide.get("mailed_at") or "", slide.get("mail_note") or ""
    if mailed and note.startswith("Envoyé"):
        return f"{note} {_fmt_stamp(mailed)} : il ne sera pas renvoyé automatiquement."
    if mailed and not note.startswith("Envoi en cours"):
        return note + "."
    if settings.get("mail_enabled") != "1":
        return "L'envoi automatique est désactivé (onglet Administration › E-mail)."
    return (f"Envoyé une seule fois, {MAIL_DELAY // 60} minutes après sa mise en diffusion (à partir de "
            f"{MAIL_HOUR} h le jour de sa date de début), aux adresses de ses entités.")


app.jinja_env.globals["mail_edit_hint"] = mail_edit_hint


def _sample_slide(settings: dict) -> dict:
    """Contenu fictif de l'e-mail de test des paramètres."""
    return {"id": None, "type": "message", "kicker": "Test d'envoi", "title": "L'envoi des e-mails fonctionne",
            "body": "Ce message de test reprend la mise en forme des contenus envoyés à leur publication.",
            "highlight": "", "highlight_label": "", "image": "", "counter_date": "",
            "entities": ",".join(e["name"] for e in entity_list(settings))}


def _send_test(r, settings: dict, to: str) -> bool:
    """Envoi de test à une adresse ; message affiché dans l'admin. Renvoie True si l'envoi a réussi."""
    db = get_db()
    try:
        refused = send_slide(r, settings, [to], test=True)
    except Exception as e:
        if not isinstance(e, (smtplib.SMTPException, OSError)):
            app.logger.exception("E-mail de test")
        err = mail_error_text(e)
        _set_mail_status(db, False, f"Test vers {to} non envoyé : {err}")
        db.commit()
        flash(f"E-mail de test non envoyé : {err}.", "error")
        return False
    if refused:
        flash(f"Adresse refusée par le serveur d'envoi : {to}.", "error")
        return False
    _set_mail_status(db, True, f"Test envoyé à {to}")
    db.commit()
    flash(f"E-mail de test envoyé à {to}. S'il n'arrive pas, regardez dans les courriers indésirables.", "ok")
    return True


@app.route("/admin/mail", methods=["POST"])
@admin_required
def mail_settings_save():
    f, db = request.form, get_db()
    settings = get_settings()
    lists, bad = {}, []
    for key in f:
        if key.startswith("to:"):
            ok, wrong = parse_addresses(f.get(key, ""))
            bad += wrong
            if ok:
                lists[key[3:].strip()[:30]] = ok
    sec = f.get("smtp_security") if f.get("smtp_security") in MAIL_SECURITY else "starttls"
    sender = f.get("mail_from", "").strip()[:254]
    values = {
        "mail_recipients": json.dumps(lists, ensure_ascii=False),
        "smtp_host": re.sub(r"\s+", "", f.get("smtp_host", ""))[:200],
        "smtp_security": sec,
        "smtp_port": str(smtp_port({"smtp_security": sec, "smtp_port": f.get("smtp_port", "")})),
        "smtp_user": f.get("smtp_user", "").strip()[:200],
        "mail_from": sender if _EMAIL.match(sender) else "",
        "mail_from_name": " ".join(f.get("mail_from_name", "").split())[:80],
        "mail_theme": f.get("mail_theme") if f.get("mail_theme") in MAIL_THEMES else "dark",
    }
    test_to = f.get("test_to", "").strip()[:254]
    if test_to:
        values["mail_test_to"] = test_to
    if not values["smtp_user"]:
        values["smtp_password"] = ""          # relais sans authentification : pas de mot de passe gardé
    elif f.get("smtp_password"):
        values["smtp_password"] = f["smtp_password"][:500]   # champ vide : mot de passe inchangé
    errors = []
    if bad:
        errors.append(f"Adresse{'s' if len(bad) > 1 else ''} ignorée{'s' if len(bad) > 1 else ''} (format invalide) : "
                      + ", ".join(bad[:5]) + (" …" if len(bad) > 5 else "") + ".")
    if sender and not values["mail_from"]:
        errors.append(f"Adresse d'expédition invalide : {sender}.")
    want, was_on = bool(f.get("mail_enabled")), settings.get("mail_enabled") == "1"
    missing = mail_config_error(settings | values)
    if want and missing:
        errors.append(f"Envoi automatique non activé : {missing}.")
        want = False
    values["mail_enabled"] = "1" if want else "0"
    for k, v in values.items():
        db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    skipped = mark_live_as_handled(db, "Non envoyé : déjà en diffusion à l'activation de l'envoi") if want and not was_on else 0
    db.commit()
    _mail_backoff.update(until=None, fails=0)   # nouveaux réglages : nouvel essai sans attendre
    for e in errors:
        flash(e, "error")
    msg = "Paramètres d'envoi enregistrés."
    if want and not was_on:
        msg = "Envoi automatique activé : chaque nouveau contenu sera envoyé à sa publication."
        if skipped:
            msg += (f" {_plural(skipped, "contenu déjà en diffusion n'est pas envoyé", 'contenus déjà en diffusion ne sont pas envoyés')}"
                    " (envoi manuel possible depuis l'aperçu de leur e-mail).")
    elif was_on and not want and not missing:
        msg = "Envoi automatique désactivé."
    flash(msg, "ok")
    if f.get("action") == "test":
        if not _EMAIL.match(test_to):
            flash("Indiquez l'adresse qui doit recevoir l'e-mail de test.", "error")
        elif missing:
            flash(f"E-mail de test non envoyé : {missing}.", "error")
        else:
            _send_test(_sample_slide(settings | values), get_settings(), test_to)
    return admin_tab("mail")


def _slide_or_404(sid: int):
    row = get_db().execute("SELECT * FROM slides WHERE id=?", (sid,)).fetchone()
    if not row:
        abort(404)
    return row


@app.route("/admin/slides/<int:sid>/mail")
@login_required
def slide_mail(sid):
    """Aperçu de l'e-mail d'un contenu, destinataires, état ; envoi de test ou envoi manuel."""
    row, settings = _slide_or_404(sid), get_settings()
    v = mail_view(row, settings)
    recipients, names = slide_recipients(row, settings)
    sender = (formataddr((settings.get("mail_from_name") or f"{v['site_name']} Affichage", settings["mail_from"]))
              if settings.get("mail_from") else "")
    return render_template("mail_preview.html", slide=dict(row), v=v, recipients=recipients, names=names,
                           sender=sender, warnings=mail_warnings(v, mail_assets(v)), badge=mail_badge(row, settings, datetime.now()),
                           mail=mail_admin_info(settings), settings=public_settings(settings), types=SLIDE_TYPES)


@app.route("/admin/slides/<int:sid>/mail/apercu")
@login_required
def slide_mail_html(sid):
    v = mail_view(_slide_or_404(sid), get_settings())
    resp = app.response_class(render_mail_html(v, mail_assets(v)), mimetype="text/html")
    resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/admin/slides/<int:sid>/mail/envoyer", methods=["POST"])
@login_required
def slide_mail_send(sid):
    row, settings, db = _slide_or_404(sid), get_settings(), get_db()
    back = redirect(url_for("slide_mail", sid=sid))
    missing = mail_config_error(settings)
    if missing:
        flash(f"Envoi impossible : {missing} (onglet Administration › E-mail).", "error")
        return back
    if request.form.get("mode") == "test":
        to = request.form.get("test_to", "").strip()[:254]
        if not _EMAIL.match(to):
            flash("Indiquez l'adresse qui doit recevoir l'e-mail de test.", "error")
            return back
        db.execute("INSERT INTO settings(key, value) VALUES('mail_test_to', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (to,))
        db.commit()
        _send_test(row, settings, to)
        return back
    recipients, names = slide_recipients(row, settings)
    if not recipients:
        flash("Aucune adresse pour " + (", ".join(names) if names else "les contenus sans entité")
              + " : complétez l'onglet Administration › E-mail.", "error")
        return back
    try:
        refused = send_slide(row, settings, recipients)
    except Exception as e:
        if not isinstance(e, (smtplib.SMTPException, OSError)):
            app.logger.exception("E-mail du contenu %s", sid)
        err = mail_error_text(e)
        _set_mail_status(db, False, f"« {_slide_label(row)} » non envoyé : {err}")
        db.commit()
        flash(f"E-mail non envoyé : {err}.", "error")
        return back
    note = _sent_note(recipients, refused)
    db.execute("UPDATE slides SET mailed_at=?, mail_note=? WHERE id=?",
               (datetime.now().isoformat(timespec="seconds"), note, sid))
    _set_mail_status(db, True, f"« {_slide_label(row)} » : {note[0].lower()}{note[1:]}")
    db.commit()
    flash(f"E-mail {note[0].lower()}{note[1:]}.", "ok")
    return back


# ------------------------------------------------------- sauvegarde : export / import
# Un seul fichier .zip : manifest.json (format, version), data.json (contenus, paramètres, thèmes
# des écrans) et uploads/ (images et vidéos utilisées). Ni le mot de passe ni la clé de session.
# L'import remplace tout, après avoir vérifié le fichier et gardé une copie de l'état précédent.
EXPORT_FORMAT = "comaxians-export"
EXPORT_VERSION = 1
EXPORT_DIR = DATA_DIR / "exports"          # copies automatiques faites avant chaque import
SAFETY_KEEP = 2                            # (les plus récentes ; elles contiennent aussi les vidéos)
_MEDIA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_SETTING_KEY = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_SAFETY_NAME = re.compile(r"^avant-import-\d{8}-\d{6}(-\d{1,3})?\.zip$")


class ImportRefused(ValueError):
    """Fichier refusé avant toute modification ; le message est affiché tel quel dans l'admin."""


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n <= 1 else many}"


def _human_size(n: float) -> str:
    for unit in ("octets", "Ko", "Mo"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "octets" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.2f} Go".replace(".", ",")


def _media_ok(name) -> bool:
    return (isinstance(name, str) and bool(_MEDIA_NAME.match(name))
            and name.rsplit(".", 1)[-1].lower() in ALLOWED_EXT)


def _used_media(db) -> list[Path]:
    names = {r[0] for r in db.execute("SELECT DISTINCT image FROM slides WHERE image != ''")}
    return [UPLOAD_DIR / n for n in sorted(names) if _media_ok(n) and (UPLOAD_DIR / n).is_file()]


def _safety_copies() -> list[Path]:
    """Copies faites avant import, la plus récente d'abord."""
    if not EXPORT_DIR.is_dir():
        return []
    return sorted((p for p in EXPORT_DIR.iterdir() if _SAFETY_NAME.match(p.name)),
                  key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def write_export(dest) -> dict:
    """Écrit la sauvegarde complète dans dest (chemin, ou fichier ouvert en écriture binaire)."""
    db = get_db()
    slides = [{k: r[k] for k in r.keys() if k != "id"}
              for r in db.execute("SELECT * FROM slides ORDER BY position, id")]
    settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")
                if r["key"] not in LOCAL_SETTINGS}   # ni le mot de passe SMTP ni l'état de l'envoi
    screens = [{"name": r["name"], "theme": r["theme"] or ""}
               for r in db.execute("SELECT name, theme FROM screens ORDER BY name")]
    media = _used_media(db)
    counts = {"slides": len(slides), "media": len(media), "screens": len(screens)}
    manifest = {"format": EXPORT_FORMAT, "version": EXPORT_VERSION,
                "created": datetime.now().isoformat(timespec="seconds"),
                "app_version": installed_version()[:7], "counts": counts}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("data.json", json.dumps({"slides": slides, "settings": settings, "screens": screens},
                                           ensure_ascii=False, indent=2))
        for p in media:   # images et vidéos, déjà compressées : stockées telles quelles
            z.write(p, f"uploads/{p.name}", compress_type=zipfile.ZIP_STORED)
    return counts


def backup_info() -> dict:
    """Résumé affiché dans la section « Sauvegarde et restauration » de l'admin."""
    db = get_db()
    media = _used_media(db)
    size = sum(p.stat().st_size for p in media)
    info = {"slides": db.execute("SELECT COUNT(*) FROM slides").fetchone()[0],
            "media": len(media), "size": _human_size(size),
            "too_big": size > app.config["MAX_CONTENT_LENGTH"],
            "max_mb": app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024), "safety": None,
            # sauvegarde chiffrée des comptes
            "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "smtp": bool((db.execute("SELECT value FROM settings WHERE key='smtp_password'").fetchone() or [""])[0])}
    copies = _safety_copies()
    if copies:
        s = copies[0].name   # avant-import-AAAAMMJJ-HHMMSS.zip
        info["safety"] = {"name": copies[0].name, "date": f"{s[19:21]}/{s[17:19]}/{s[13:17]} à {s[22:24]}:{s[24:26]}"}
    return info


def _read_import(z: zipfile.ZipFile) -> tuple[dict, dict]:
    names = set(z.namelist())
    not_ours = ImportRefused("Ce fichier n'est pas une sauvegarde de l'affichage : choisissez un fichier "
                             "obtenu avec « Télécharger la sauvegarde ».")
    if len(names) > 5000 or "manifest.json" not in names:
        raise not_ours
    try:
        manifest = json.loads(z.read("manifest.json"))
    except ValueError:
        raise not_ours from None
    if not isinstance(manifest, dict) or manifest.get("format") != EXPORT_FORMAT:
        raise not_ours
    if not isinstance(manifest.get("version"), int) or manifest["version"] > EXPORT_VERSION:
        raise ImportRefused("Sauvegarde faite avec une version plus récente de l'application : "
                            "mettez d'abord ce serveur à jour, puis recommencez l'import.")
    damaged = ImportRefused("Sauvegarde incomplète ou endommagée : rien n'a été modifié.")
    if "data.json" not in names or z.getinfo("data.json").file_size > 50 * 1024 * 1024:
        raise damaged
    try:
        data = json.loads(z.read("data.json"))
    except ValueError:
        raise damaged from None
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
        raise damaged
    return manifest, data


def _prepare_import(z: zipfile.ZipFile, data: dict) -> dict:
    """Vérifie et nettoie le contenu du fichier, sans rien modifier."""
    db = get_db()
    cols = [r[1] for r in db.execute("PRAGMA table_info(slides)") if r[1] not in ("id", "position")]
    names = set(z.namelist())
    slides, media, skipped, missing = [], {}, 0, 0
    for s in data["slides"]:
        if not isinstance(s, dict) or s.get("type") not in SLIDE_TYPES:
            skipped += 1   # type inconnu de cette version de l'application
            continue
        row = {}
        for c in cols:   # colonnes inconnues ignorées ; colonnes absentes : valeur par défaut
            v = s.get(c)
            if isinstance(v, bool):
                row[c] = int(v)
            elif isinstance(v, str):
                row[c] = v[:200_000]
            elif isinstance(v, (int, float)):
                row[c] = v
        row["title"] = row.get("title") or ""
        if "mail_on_publish" not in s:   # sauvegarde antérieure à l'envoi par e-mail
            row["mail_on_publish"] = 0 if row["type"] in ("video", "anniversaire") else 1
        img = row.get("image", "")
        if img and _media_ok(img) and f"uploads/{img}" in names:
            media[img] = z.getinfo(f"uploads/{img}")
        elif img:
            row["image"] = ""
            missing += 1
            if row["type"] in TITLE_OPTIONAL:   # vidéo ou affiche sans son fichier : rien à afficher
                row["active"] = 0
        slides.append(row)
    settings = {}
    if isinstance(data.get("settings"), dict):
        for k, v in data["settings"].items():
            if k in LOCAL_SETTINGS:   # propres à ce serveur : conservés tels quels
                continue
            if _SETTING_KEY.match(str(k)) and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                settings[k] = str(v)[:20_000]
    screens = {}
    for sc in data.get("screens") if isinstance(data.get("screens"), list) else []:
        if isinstance(sc, dict) and isinstance(sc.get("name"), str) and normalize_screen(sc["name"]):
            screens[normalize_screen(sc["name"])] = sc.get("theme") if sc.get("theme") in THEMES else ""
    if data["slides"] and not slides:
        raise ImportRefused("Aucun contenu utilisable dans ce fichier : rien n'a été modifié.")
    # Place nécessaire : visuels importés + copie de l'état actuel (qui contient les visuels actuels)
    need = (sum(i.file_size for i in media.values()) + sum(p.stat().st_size for p in _used_media(db))
            + 50 * 1024 * 1024)
    if shutil.disk_usage(DATA_DIR).free < need:
        raise ImportRefused(f"Espace disque insuffisant sur le serveur ({_human_size(need)} nécessaires) : "
                            "rien n'a été modifié.")
    return {"slides": slides, "media": media, "settings": settings, "screens": screens,
            "skipped": skipped, "missing": missing}


def _safety_copy() -> Path:
    """Sauvegarde de l'état actuel, faite juste avant un import."""
    EXPORT_DIR.mkdir(exist_ok=True)
    stamp = f"avant-import-{datetime.now():%Y%m%d-%H%M%S}"
    dest, n = EXPORT_DIR / f"{stamp}.zip", 1
    while dest.exists():   # deux imports dans la même seconde
        n += 1
        dest = EXPORT_DIR / f"{stamp}-{n}.zip"
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        write_export(tmp)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _apply_import(z: zipfile.ZipFile, prep: dict):
    """Remplace contenus, paramètres et thèmes des écrans, en une seule transaction."""
    db = get_db()
    written, mapping = [], {}
    try:
        for old, info in prep["media"].items():   # nouveaux noms : aucun conflit avec les fichiers en place
            new = f"{uuid.uuid4().hex}.{old.rsplit('.', 1)[-1].lower()}"
            with z.open(info) as src, open(UPLOAD_DIR / new, "wb") as dst:
                written.append(new)
                shutil.copyfileobj(src, dst, 1024 * 1024)
            mapping[old] = new
        old_images = {r[0] for r in db.execute("SELECT DISTINCT image FROM slides WHERE image != ''")}
        now = datetime.now().isoformat(timespec="seconds")
        db.execute("DELETE FROM slides")
        for pos, row in enumerate(prep["slides"]):
            row = {"updated_at": now} | row | {"position": pos, "image": mapping.get(row.get("image", ""), "")}
            db.execute(f"INSERT INTO slides({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                       list(row.values()))
        # Paramètres absents du fichier : valeur par défaut (sauf ceux propres à ce serveur, conservés)
        for k in (DEFAULT_SETTINGS.keys() | prep["settings"].keys()) - LOCAL_SETTINGS:
            db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (k, prep["settings"].get(k, DEFAULT_SETTINGS.get(k))))
        # Contenus déjà en diffusion : pas d'e-mail (ils ont été publiés avant, sur l'autre serveur)
        mark_live_as_handled(db, "Non envoyé : contenu importé déjà en diffusion")
        # Écrans : thèmes du fichier ; les écrans qui n'y sont pas réapparaîtront d'eux-mêmes
        names = list(prep["screens"])
        db.execute(f"DELETE FROM screens WHERE name NOT IN ({', '.join('?' * len(names))})", names)
        for n, theme in prep["screens"].items():
            db.execute("INSERT INTO screens(name, theme) VALUES (?, ?) "
                       "ON CONFLICT(name) DO UPDATE SET theme=excluded.theme", (n, theme))
        db.commit()
    except BaseException:
        db.rollback()
        for n in written:
            delete_upload(n)
        raise
    for n in old_images:   # anciens visuels, plus utilisés
        release_upload(db, n)


@app.route("/admin/export")
@admin_required
def export_data():
    EXPORT_DIR.mkdir(exist_ok=True)
    tmp = tempfile.TemporaryFile(dir=EXPORT_DIR)   # sur le disque, pas en mémoire (vidéos)
    try:
        write_export(tmp)
    except BaseException:
        tmp.close()
        raise
    tmp.seek(0)
    return send_file(tmp, mimetype="application/zip", as_attachment=True, max_age=0,
                     download_name=f"axians-affichage-{datetime.now():%Y-%m-%d-%Hh%M}.zip")


@app.route("/admin/export/copie/<name>")
@admin_required
def export_safety(name):
    if not _SAFETY_NAME.match(name):
        abort(404)
    return send_from_directory(EXPORT_DIR, name, as_attachment=True, max_age=0)


@app.route("/admin/import", methods=["POST"])
@admin_required
def import_data():
    back = admin_tab("sauvegarde")   # fichier refusé : message dans la section Sauvegarde
    f = request.files.get("archive")
    if not f or not f.filename:
        flash("Choisissez le fichier de sauvegarde (.zip) à importer.", "error")
        return back
    try:
        with zipfile.ZipFile(f.stream) as z:
            manifest, data = _read_import(z)
            prep = _prepare_import(z, data)
            safety = _safety_copy()
            try:
                _apply_import(z, prep)
            except BaseException:
                safety.unlink(missing_ok=True)   # rien n'a changé : copie inutile
                raise
        for old in _safety_copies()[SAFETY_KEEP:]:   # import réussi : on ne garde que les plus récentes
            old.unlink(missing_ok=True)
    except ImportRefused as e:
        flash(str(e), "error")
        return back
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, NotImplementedError, RuntimeError):
        # RuntimeError : archive protégée par mot de passe ; NotImplementedError : compression inconnue
        flash("Ce fichier n'est pas une sauvegarde de l'affichage, ou il est endommagé : rien n'a été modifié.", "error")
        return back
    except OSError as e:
        app.logger.error("Import impossible : %s", e)
        flash(f"Import impossible ({e.strerror or e}) : rien n'a été modifié.", "error")
        return back
    created = str(manifest.get("created", ""))
    msg = (f"Sauvegarde{f' du {created[8:10]}/{created[5:7]}/{created[:4]}' if len(created) >= 10 else ''} importée : "
           f"{_plural(len(prep['slides']), 'contenu', 'contenus')}, "
           f"{_plural(len(prep['media']), 'image ou vidéo', 'images ou vidéos')}, paramètres et thèmes des écrans.")
    if prep["skipped"]:
        msg += f" {_plural(prep['skipped'], 'contenu ignoré', 'contenus ignorés')} (type inconnu de cette version)."
    if prep["missing"]:
        msg += f" {_plural(prep['missing'], 'visuel manquait', 'visuels manquaient')} dans le fichier."
    flash(msg + " Les écrans se mettent à jour d'eux-mêmes en moins d'une minute.", "ok")
    return redirect(url_for("admin"))   # importé : la liste des contenus importés, avec le message


# ------------------------------------------------- sauvegarde chiffrée des comptes et mots de passe
# Fichier séparé de la sauvegarde .zip : comptes utilisateurs (mots de passe hachés) et mot de passe SMTP,
# chiffrés avec un mot de passe maître. Bibliothèque standard uniquement (rien à installer sur le Pi) :
#   clés     : scrypt(mot de passe maître, sel aléatoire) → 64 octets = clé de chiffrement + clé d'authentification
#   chiffre  : HMAC-SHA256 en mode compteur (flux pseudo-aléatoire, nonce aléatoire) combiné par XOR
#   intégrité: HMAC-SHA256 sur l'en-tête et le texte chiffré, vérifié avant tout déchiffrement
# Mauvais mot de passe maître ou fichier modifié : refusé, rien n'est changé.
SECRETS_FORMAT = "comaxians-comptes"
SECRETS_VERSION = 1
SECRETS_KDF = {"n": 2 ** 15, "r": 8, "p": 1}
SECRETS_MAX_BYTES = 2 * 1024 * 1024
MASTER_MIN = 12


def _secrets_keys(master: str, salt: bytes, n: int, r: int, p: int) -> tuple[bytes, bytes]:
    key = hashlib.scrypt(master.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=64)
    return key[:32], key[32:]


def _keystream_xor(key: bytes, nonce: bytes, data: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(data), 32):
        block = hmac.new(key, nonce + (i // 32).to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[i:i + 32]
        out += (int.from_bytes(chunk, "big") ^ int.from_bytes(block[:len(chunk)], "big")).to_bytes(len(chunk), "big")
    return bytes(out)


def _secrets_header(n: int, r: int, p: int, salt: bytes, nonce: bytes) -> bytes:
    return f"{SECRETS_FORMAT}/{SECRETS_VERSION}/scrypt/{n}/{r}/{p}/".encode() + salt + nonce


def encrypt_secrets(payload: dict, master: str) -> dict:
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    kdf = SECRETS_KDF
    enc_key, mac_key = _secrets_keys(master, salt, **kdf)
    data = _keystream_xor(enc_key, nonce, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    tag = hmac.new(mac_key, _secrets_header(kdf["n"], kdf["r"], kdf["p"], salt, nonce) + data, hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")   # noqa: E731
    return {"format": SECRETS_FORMAT, "version": SECRETS_VERSION,
            "created": datetime.now().isoformat(timespec="seconds"),
            "kdf": {"name": "scrypt", **kdf, "salt": b64(salt)},
            "cipher": "hmac-sha256-ctr", "mac": "hmac-sha256",
            "nonce": b64(nonce), "data": b64(data), "tag": b64(tag)}


def decrypt_secrets(env: dict, master: str) -> dict:
    """Vérifie puis déchiffre un fichier de comptes ; ImportRefused si illisible, modifié ou mauvais mot de passe."""
    if not isinstance(env, dict) or env.get("format") != SECRETS_FORMAT:
        raise ImportRefused("Ce fichier n'est pas une sauvegarde des comptes de l'affichage : rien n'a été modifié.")
    if env.get("version") != SECRETS_VERSION:
        raise ImportRefused("Sauvegarde des comptes d'une version plus récente de l'application : mettez d'abord ce "
                            "serveur à jour. Rien n'a été modifié.")
    try:
        kdf = env["kdf"]
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
        salt, nonce = base64.b64decode(kdf["salt"], validate=True), base64.b64decode(env["nonce"], validate=True)
        data, tag = base64.b64decode(env["data"], validate=True), base64.b64decode(env["tag"], validate=True)
    except (KeyError, TypeError, ValueError):
        raise ImportRefused("Sauvegarde des comptes endommagée : rien n'a été modifié.") from None
    # Paramètres bornés : un fichier fabriqué ne peut pas faire calculer le serveur indéfiniment
    if kdf.get("name") != "scrypt" or n not in (2 ** 14, 2 ** 15, 2 ** 16) or r != 8 or p != 1 \
            or len(salt) != 16 or len(nonce) != 16 or len(tag) != 32:
        raise ImportRefused("Sauvegarde des comptes endommagée : rien n'a été modifié.")
    enc_key, mac_key = _secrets_keys(master, salt, n, r, p)
    expected = hmac.new(mac_key, _secrets_header(n, r, p, salt, nonce) + data, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise ImportRefused("Mot de passe maître incorrect (ou fichier modifié) : rien n'a été modifié.")
    try:
        payload = json.loads(_keystream_xor(enc_key, nonce, data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ImportRefused("Sauvegarde des comptes endommagée : rien n'a été modifié.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
        raise ImportRefused("Sauvegarde des comptes endommagée : rien n'a été modifié.")
    return payload


def accounts_payload() -> dict:
    db = get_db()
    users = [{"username": r["username"], "name": r["name"] or "", "role": r["role"], "password": r["password"],
              "builtin": bool(r["builtin"]), "created_at": r["created_at"] or ""}
             for r in db.execute("SELECT * FROM users ORDER BY id")]
    smtp = db.execute("SELECT value FROM settings WHERE key='smtp_password'").fetchone()
    return {"created": datetime.now().isoformat(timespec="seconds"), "app_version": installed_version()[:7],
            "users": users, "smtp_password": (smtp[0] if smtp else "") or ""}


def _valid_account(u) -> bool:
    return (isinstance(u, dict) and isinstance(u.get("username"), str) and bool(_USERNAME.match(u["username"]))
            and u.get("role") in ROLES and isinstance(u.get("name", ""), str)
            and isinstance(u.get("password"), str) and 10 <= len(u["password"]) <= 400
            and u["password"].startswith(("scrypt:", "pbkdf2:")) and "$" in u["password"])


def restore_accounts(payload: dict) -> dict:
    """Comptes du fichier recréés ou remis à l'identique (nom, rôle, mot de passe) ; les comptes absents du fichier
    sont gardés. Le compte d'origine reste administrateur et le mien garde son rôle (comme dans la section
    Utilisateurs). Mot de passe remis : les autres sessions de ce compte sont fermées."""
    db, me = get_db(), current_user()
    now = datetime.now().isoformat(timespec="seconds")
    res = {"created": 0, "updated": 0, "skipped": 0, "smtp": False, "mine": False}
    for u in payload["users"][:500]:
        if not _valid_account(u):
            res["skipped"] += 1
            continue
        name = " ".join(u.get("name", "").split())[:60]
        row = db.execute("SELECT * FROM users WHERE username=?", (u["username"],)).fetchone()
        if row is None:
            db.execute("INSERT INTO users(username, name, role, password, token, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (u["username"].lower(), name, u["role"], u["password"], secrets.token_hex(16),
                        str(u.get("created_at") or now)[:19]))
            res["created"] += 1
            continue
        role = row["role"] if (row["builtin"] or row["id"] == me["id"]) else u["role"]
        db.execute("UPDATE users SET name=?, role=? WHERE id=?", (name, role, row["id"]))
        if u["password"] != row["password"]:
            db.execute("UPDATE users SET password=? WHERE id=?", (u["password"], row["id"]))
            _rotate_token(db, row["id"])
            res["mine"] = res["mine"] or row["id"] == me["id"]
        res["updated"] += 1
    smtp = payload.get("smtp_password")
    if isinstance(smtp, str) and smtp and len(smtp) <= 500:
        db.execute("INSERT INTO settings(key, value) VALUES ('smtp_password', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (smtp,))
        res["smtp"] = True
    db.commit()
    return res


@app.route("/admin/comptes/export", methods=["POST"])
@admin_required
def accounts_export():
    master, confirm = request.form.get("master", ""), request.form.get("master_confirm", "")
    if len(master) < MASTER_MIN:
        flash(f"Mot de passe maître trop court ({MASTER_MIN} caractères minimum) : aucun fichier créé.", "error")
        return admin_tab("sauvegarde")
    if master != confirm or len(master) > 200:
        flash("Les deux mots de passe maîtres saisis sont différents : aucun fichier créé.", "error")
        return admin_tab("sauvegarde")
    env = encrypt_secrets(accounts_payload(), master)
    body = io.BytesIO(json.dumps(env, indent=2).encode("utf-8"))
    return send_file(body, mimetype="application/json", as_attachment=True, max_age=0,
                     download_name=f"axians-affichage-comptes-{datetime.now():%Y-%m-%d-%Hh%M}.json")


@app.route("/admin/comptes/import", methods=["POST"])
@admin_required
def accounts_import():
    f = request.files.get("comptes")
    master = request.form.get("master", "")
    if not f or not f.filename:
        flash("Choisissez le fichier de sauvegarde des comptes (.json) à restaurer.", "error")
        return admin_tab("sauvegarde")
    raw = f.stream.read(SECRETS_MAX_BYTES + 1)
    try:
        if len(raw) > SECRETS_MAX_BYTES:
            raise ImportRefused("Fichier trop volumineux pour une sauvegarde des comptes : rien n'a été modifié.")
        try:
            env = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ImportRefused("Ce fichier n'est pas une sauvegarde des comptes de l'affichage : "
                                "rien n'a été modifié.") from None
        payload = decrypt_secrets(env, master)
    except ImportRefused as e:
        flash(str(e), "error")
        return admin_tab("sauvegarde")
    res = restore_accounts(payload)
    parts = [_plural(res["created"], "compte recréé", "comptes recréés"),
             _plural(res["updated"], "compte remis à l'identique", "comptes remis à l'identique")]
    msg = "Comptes restaurés : " + ", ".join(parts)
    if res["smtp"]:
        msg += ", mot de passe du serveur d'envoi des e-mails"
    msg += "."
    if res["skipped"]:
        msg += f" {_plural(res['skipped'], 'compte ignoré', 'comptes ignorés')} (illisible par cette version)."
    if res["mine"]:
        msg += " Votre mot de passe est désormais celui du fichier."
    flash(msg, "ok")
    return admin_tab("utilisateurs")


@app.errorhandler(413)
def too_large(_e):
    limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
    flash(f"Fichier trop volumineux : {limit} Mo au maximum (réglable sur le serveur avec "
          "COMAXIANS_MAX_UPLOAD_MB).", "error")
    return redirect(request.referrer or url_for("admin"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

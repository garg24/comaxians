"""ComAxians – affichage dynamique interne aux couleurs d'Axians.

Un serveur central (cette application) héberge :
  * /admin   : interface de gestion des contenus (succès, actualités, partenaires…)
  * /display : le "player" plein écran ouvert par chaque Raspberry Pi en mode kiosque
  * /api/playlist : la liste des diapositives actives, interrogée périodiquement par les écrans
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

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
}
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
ADMIN_PASSWORD = os.environ.get("COMAXIANS_ADMIN_PASSWORD", "axians")


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
    updated_at  TEXT
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
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    if new_database:
        _seed(db)
    db.commit()
    db.close()


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
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
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
    return {"ENTITIES": entity_list() if request.endpoint not in ("display", "uploaded", "static") else []}


@app.before_request
def check_csrf():
    if request.method == "POST" and request.endpoint != "login":
        if request.form.get("csrf") != session.get("csrf"):
            abort(400, "Jeton CSRF invalide")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session.clear()
            session["admin"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("admin"))
        flash("Mot de passe incorrect.", "error")
    return render_template("login.html")


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
    if preview and session.get("admin"):
        rows = [r for r in rows if r["id"] == preview]
    else:
        rows = [r for r in rows if is_live(r, today) and targets_screen(r, screen)]
    entities = entity_list(settings)
    slides = [slide_to_dict(r, default_duration, entities) for r in rows]
    if preview and session.get("admin"):
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
    for s in slides:   # une diapositive Anniversaires n'est diffusée que les jours d'anniversaire
        if s["bday"] is not None and not s["bday"]["today"]:
            s["waiting"] = s["live"]
            s["live"] = False
    return render_template("admin.html", slides=slides, types=SLIDE_TYPES, settings=get_settings(),
                           screens=list_screens(), themes=THEMES, theme_icons=THEME_ICONS)


@app.route("/admin/screens", methods=["POST"])
@login_required
def screen_save():
    name = normalize_screen(request.form.get("name", ""))
    theme = request.form.get("theme", "")
    if theme not in THEMES:
        theme = ""  # "" = suit le thème par défaut
    if not name:
        flash("Indiquez le nom de l'écran.", "error")
        return redirect(url_for("admin") + "#ecrans")
    db = get_db()
    db.execute("INSERT INTO screens(name, theme) VALUES (?, ?) "
               "ON CONFLICT(name) DO UPDATE SET theme=excluded.theme", (name, theme))
    db.commit()
    flash(f"Écran « {name} » enregistré. Le changement s'applique dans la minute.", "ok")
    return redirect(url_for("admin") + "#ecrans")


@app.route("/admin/screens/delete", methods=["POST"])
@login_required
def screen_delete():
    get_db().execute("DELETE FROM screens WHERE name=?", (normalize_screen(request.form.get("name", "")),))
    get_db().commit()
    return redirect(url_for("admin") + "#ecrans")


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
    db.execute("UPDATE slides SET active = 1 - active WHERE id=?", (sid,))
    db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/slides/<int:sid>/move/<direction>", methods=["POST"])
@login_required
def slide_move(sid, direction):
    db = get_db()
    ids = [r["id"] for r in db.execute("SELECT id FROM slides ORDER BY position, id")]
    if sid in ids:
        i = ids.index(sid)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            for pos, x in enumerate(ids):
                db.execute("UPDATE slides SET position=? WHERE id=?", (pos, x))
            db.commit()
    return redirect(url_for("admin"))


@app.route("/admin/settings", methods=["POST"])
@login_required
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
    return redirect(url_for("admin"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)

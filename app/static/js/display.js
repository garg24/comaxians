/* Player d'affichage dynamique Axians.
 * Paramètres d'URL :
 *   screen=<nom>   n'affiche que les contenus destinés à cet écran (+ ceux destinés à tous)
 *   preview=<id>   aperçu d'une diapositive (nécessite d'être connecté à l'admin)
 *   lite=1         allège l'affichage (Raspberry Pi 3 et antérieurs) : animations désactivées,
 *                  effets coûteux retirés, fondu enchaîné plus court
 *   theme=light|dark|alternate  force un thème (sinon : réglage de l'écran dans l'admin)
 */
(() => {
  "use strict";
  const params = new URLSearchParams(location.search);
  const SCREEN = params.get("screen") || "";
  const PREVIEW = params.get("preview") || "";
  const POLL_MS = 60 * 1000;
  const MAX_VIDEO_MS = 15 * 60 * 1000;     // garde-fou : une vidéo ne bloque jamais l'écran
  const CACHE_KEY = "axians-playlist-" + SCREEN;
  const BOOT = Date.now();
  const THEME_MODES = ["light", "dark", "alternate"];
  const FORCED_THEME = THEME_MODES.includes(params.get("theme")) ? params.get("theme") : "";

  const $ = (id) => document.getElementById(id);
  const stage = $("stage"), content = $("content");
  const LITE = params.get("lite") === "1";
  if (LITE) document.body.classList.add("lite");

  /* Transitions : la diapositive suivante est préparée à l'avance (voir « transitions » plus bas) */
  const FADE_MS = LITE ? 500 : 800;     // fondu enchaîné, plus court sur Raspberry Pi 3
  const PREP_LEAD_MS = 2000;            // préparation de la diapositive suivante, avant son passage
  const READY_MAX_MS = 3000;            // une image ou une vidéo lente ne bloque jamais le diaporama
  document.documentElement.style.setProperty("--fade", FADE_MS + "ms");

  let slides = [], settings = {}, version = null, pending = null;
  let idx = -1, timer = null, prepTimer = null, current = null, prepared = null, seq = 0;

  /* ------------------------------------------------------------------ thèmes */
  let themeMode = "dark";   // "dark" | "light" | "alternate" (une diapositive sur deux)
  let shown = 0;            // nombre de diapositives affichées : sert à l'alternance

  function themeNode(node, t) {
    node.classList.toggle("theme-light", t === "light");
    node.classList.toggle("theme-dark", t !== "light");
  }
  function setSurface(t, node) {
    // node : diapositive à habiller ; l'en-tête et le pied suivent via la classe de .stage
    stage.classList.toggle("theme-light", t === "light");
    if (node) themeNode(node, t);
  }
  function nextSlideTheme() {
    if (themeMode !== "alternate") return themeMode;
    return shown++ % 2 === 0 ? "dark" : "light";
  }
  function applyTheme(theme) {
    themeMode = FORCED_THEME || (THEME_MODES.includes(theme) ? theme : "dark");
    // Sans alternance, on rhabille immédiatement la diapositive en cours
    if (themeMode !== "alternate") setSurface(themeMode, current);
  }
  applyTheme("");

  /* --------------------------------------------------------- mise à l'échelle */
  function fit() {
    // Taille réellement visible : visualViewport quand il existe (sur certains moteurs web Android,
    // innerWidth décrit une zone de mise en page plus large que l'écran), sinon innerWidth.
    const vv = window.visualViewport;
    const w = (vv && vv.width) || innerWidth || document.documentElement.clientWidth;
    const h = (vv && vv.height) || innerHeight || document.documentElement.clientHeight;
    if (!w || !h) return;
    const s = Math.min(w / 1920, h / 1080);
    stage.style.transform = `translate(${(w - 1920 * s) / 2}px, ${(h - 1080 * s) / 2}px) scale(${s})`;
  }
  addEventListener("resize", fit);
  addEventListener("load", fit);
  if (window.visualViewport) visualViewport.addEventListener("resize", fit);
  // Sur certaines box, la taille définitive n'est connue qu'après le passage en plein écran
  [0, 500, 2000].forEach((ms) => setTimeout(fit, ms));

  /* ------------------------------------------------------------------ horloge */
  const fmtTime = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit" });
  const fmtDate = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });
  function tick() {
    const now = new Date();
    $("clock-time").textContent = fmtTime.format(now);
    $("clock-date").textContent = fmtDate.format(now);
    // Rechargement quotidien (vers 4 h) pour libérer la mémoire sur les Raspberry Pi
    if (now.getHours() === 4 && Date.now() - BOOT > 3 * 3600 * 1000) location.reload();
  }
  setInterval(tick, 1000);
  tick();

  /* --------------------------------------------------------- construction DOM */
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null && text !== "") n.textContent = text;
    return n;
  }

  /* Ligne d'étiquettes : surtitre (ex. « Nouveau contrat ») + entités concernées (ex. Cloud, Communication) */
  function tagsRow(s, animated = true) {
    const ents = s.entities || [];
    if (!s.kicker && !ents.length) return null;
    const row = el("div", "tags" + (animated ? " a" : ""));
    if (s.kicker) row.append(el("span", "tag", s.kicker));
    ents.forEach((e) => {
      const t = el("span", "ent", e.name);
      t.style.setProperty("--ec", e.color);
      row.append(t);
    });
    return row;
  }

  /* Compteur automatique : jours écoulés depuis une date (heure locale de l'écran).
     Recalculé à chaque passage de la diapositive, donc à jour chaque jour, même hors ligne. */
  /* ------------------------------------------------------------ anniversaires */
  // Le serveur n'envoie que les personnes fêtées aujourd'hui ou demain ; l'écran garde celles du jour
  // selon sa propre horloge (bascule à minuit, même hors ligne).
  function isLeap(y) { return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0; }
  function celebratedToday(p, now = new Date()) {
    const d = now.getDate(), m = now.getMonth() + 1;
    if (p.month === 2 && p.day === 29 && !isLeap(now.getFullYear())) return m === 2 && d === 28;
    return p.month === m && p.day === d;
  }
  function birthdayPeople(s) {
    const list = s.birthdays || [];
    return s.preview_any_day ? list : list.filter((p) => celebratedToday(p));
  }
  // Une diapositive « Anniversaires » n'est jouée que si quelqu'un est fêté aujourd'hui
  function playable(s) {
    return !!s && (s.type !== "anniversaire" || birthdayPeople(s).length > 0);
  }

  const BALLOON_COLORS = [["#FF3D9A", "#FF8CC6"], ["#F5B301", "#FFE08A"], ["#2F6BFF", "#8DB0FF"], ["#22D3EE", "#A5F3FC"], ["#8B5CF6", "#C4B5FD"]];
  function balloons(side) {
    // SVG statique (aucune donnée saisie) : 3 ballons avec ficelles
    const pick = side === "left" ? [0, 1, 2] : [3, 4, 0];
    const b = (cx, cy, k) => {
      const [c1, c2] = BALLOON_COLORS[k];
      return `<path d="M${cx} ${cy + 72} q -18 60 6 120 t -4 140" fill="none" stroke="currentColor" stroke-opacity=".35" stroke-width="2"/>
        <ellipse cx="${cx}" cy="${cy}" rx="56" ry="70" style="fill:${c1}"/>
        <ellipse cx="${cx - 18}" cy="${cy - 26}" rx="14" ry="22" style="fill:${c2}" opacity=".7"/>
        <path d="M${cx - 8} ${cy + 74} l 8 -8 l 8 8 z" style="fill:${c1}"/>`;
    };
    const d = el("div", "balloons " + side);
    d.innerHTML = `<svg viewBox="0 0 260 640" width="260" height="640" aria-hidden="true">
      ${b(70, 120, pick[0])}${b(185, 210, pick[1])}${b(95, 330, pick[2])}</svg>`;
    return d;
  }
  function confetti(n = 28) {
    const box = el("div", "confetti");
    const colors = ["#FF3D9A", "#F5B301", "#2F6BFF", "#22D3EE", "#8B5CF6", "#22C55E"];
    for (let i = 0; i < n; i++) {
      const c = el("i");
      c.style.left = Math.round(Math.random() * 100) + "%";
      c.style.background = colors[i % colors.length];
      c.style.animationDelay = (-Math.random() * 8).toFixed(2) + "s";
      c.style.animationDuration = (6 + Math.random() * 5).toFixed(2) + "s";
      c.style.setProperty("--r", Math.round(Math.random() * 360) + "deg");
      box.append(c);
    }
    return box;
  }

  /* Initiales d'un nom (« Marie Dupont » → « MD »), affichées quand il n'y a pas de photo */
  function initials(name) {
    const parts = (name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "★";
    const first = parts[0][0], last = parts.length > 1 ? parts[parts.length - 1][0] : "";
    return (first + last).toUpperCase();
  }

  function daysSince(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.max(0, Math.round((today - new Date(y, m - 1, d)) / 86400000));
  }

  function videoEl(src, { loop = false, sound = false, cls = "" } = {}) {
    const v = el("video", cls);
    v.src = src;
    v.muted = !sound;
    v.loop = loop;
    v.playsInline = true;
    v.preload = "auto";     // chargée pendant la préparation, lue au moment où la diapositive apparaît
    return v;
  }

  function mediaEl(s, cls) {
    if (!s.media) return null;
    if (s.media_type === "video") return videoEl(s.media, { loop: true, cls });
    const img = el("img", cls);
    img.src = s.media; img.alt = "";
    return img;
  }

  function bg(withGrid = true) {
    const frag = [el("div", "glow")];
    if (withGrid) frag.push(el("div", "grid"));
    return frag;
  }

  function textBlock(s, { stat = true } = {}) {
    const col = el("div", "text");
    { const tr = tagsRow(s); if (tr) col.append(tr); }
    if (s.title) col.append(el("h1", "title a", s.title));
    if (s.body) col.append(el("p", "body a", s.body));
    if (stat && s.highlight) {
      const k = el("div", "stat a");
      k.append(el("span", "stat-value grad-text", s.highlight));
      if (s.highlight_label) k.append(el("span", "stat-label", s.highlight_label));
      col.append(k);
    }
    return col;
  }

  function visual(s) {
    const m = mediaEl(s);
    if (m) { const box = el("div", "media"); box.append(m); return [box]; }
    return [el("div", "orb pop"), el("div", "orb-core pop")];
  }

  const builders = {
    succes: (s) => [...bg(), ...visual(s), textBlock(s)],
    actualite: (s) => [...bg(), ...visual(s), textBlock(s)],
    partenaire(s) {
      if (!s.media || s.media_type === "video") return [...bg(), ...visual(s), textBlock(s)];
      const card = el("div", "logo-card pop");
      const img = el("img"); img.src = s.media; img.alt = "";
      card.append(img);
      return [...bg(), card, textBlock(s)];
    },
    evenement(s) {
      const parts = [...bg()];
      const m = mediaEl(s);
      if (m) { const box = el("div", "media"); box.append(m); parts.push(box); }
      if (s.highlight) {
        const card = el("div", "date-card pop");
        card.append(el("div", "date-day grad-text", s.highlight));
        if (s.highlight_label) card.append(el("div", "date-month", s.highlight_label));
        parts.push(card);
      }
      parts.push(textBlock(s, { stat: false }));
      return parts;
    },
    anniversaire(s) {
      const people = birthdayPeople(s);
      const MAX = 9;
      const c = el("div", "center");
      { const tr = tagsRow(s); if (tr) c.append(tr); }
      c.append(el("h1", "title bday-title grad-text a", s.title || "Joyeux anniversaire !"));
      const list = el("div", "bday-names a" + (people.length === 1 ? " solo" : people.length > 4 ? " many" : ""));
      people.slice(0, MAX).forEach((p) => {
        const card = el("div", "bday-card");
        card.append(el("div", "bday-avatar", initials(`${p.first} ${p.last}`)));
        const name = el("div", "bday-name");
        name.append(el("span", "first", p.first), document.createTextNode(" "), el("span", "last", p.last));
        card.append(name);
        list.append(card);
      });
      if (people.length > MAX) list.append(el("div", "bday-card more", `+ ${people.length - MAX}`));
      c.append(list);
      if (s.body) c.append(el("p", "body a", s.body));
      return [...bg(false), confetti(), balloons("left"), balloons("right"), c];
    },
    employe(s) {
      // Portrait rond entouré d'un anneau, badge de la période, quelques étincelles
      const wrap = el("div", "portrait-wrap pop");
      wrap.append(el("div", "portrait-ring"));
      const p = el("div", "portrait");
      if (s.media && s.media_type === "image") {
        const img = el("img"); img.src = s.media; img.alt = "";
        p.append(img);
      } else {
        p.append(el("div", "initials", initials(s.title)));
      }
      wrap.append(p);
      ["s1", "s2", "s3", "s4"].forEach((c) => wrap.append(el("span", "sparkle " + c, "✦")));
      if (s.highlight) wrap.append(el("div", "award", "★ " + s.highlight));

      const col = el("div", "text");
      { const tr = tagsRow(s); if (tr) col.append(tr); }
      col.append(el("h1", "title a", s.title));
      if (s.highlight_label) col.append(el("div", "role grad-text a", s.highlight_label));
      if (s.body) col.append(el("p", "body quote a", s.body));
      return [...bg(), wrap, col];
    },
    chiffre(s) {
      const c = el("div", "center");
      { const tr = tagsRow(s); if (tr) c.append(tr); }
      const value = s.counter_date ? daysSince(s.counter_date).toLocaleString("fr-FR") : (s.highlight || "—");
      c.append(el("div", "big-number grad-text a", value));
      c.append(el("div", "big-label a", s.highlight_label ? `${s.title} · ${s.highlight_label}` : s.title));
      if (s.body) c.append(el("p", "body a", s.body));
      return [...bg(false), c];
    },
    message(s) {
      const c = el("div", "center");
      c.append(el("div", "quote-mark grad-text a", "“"));
      c.append(el("h1", "title a", s.title));
      { const tr = tagsRow(s); if (tr) c.append(tr); }
      if (s.body) c.append(el("p", "body a", s.body));
      return [...bg(), c];
    },
    image(s) {
      const p = el("div", "poster pop");
      if (s.media) {
        const m = mediaEl(s);
        if (m.tagName === "VIDEO") m.loop = true;
        p.append(m);
      }
      return [...bg(false), p];
    },
    video(s) {
      const parts = [];
      if (s.media) parts.push(videoEl(s.media, { sound: s.sound, cls: "full", loop: !!s.duration }));
      if (s.title || s.kicker) {
        const lt = el("div", "lower-third");
        { const tr = tagsRow(s, false); if (tr) lt.append(tr); }
        if (s.title) lt.append(el("div", "title", s.title));
        if (s.body) lt.append(el("p", "body", s.body));
        parts.push(lt);
      }
      return parts;
    },
  };

  /* Réduit la taille de police tant que le texte déborde de sa zone */
  /* --------------------------------------------- ajustement du texte à la place disponible
     Chaque zone de texte (colonne, centre, carte date, bandeau vidéo) est réduite d'un même
     facteur jusqu'à ce que tout tienne, quelle que soit la longueur des textes ou la police.
     En dernier recours, le texte est abrégé proprement (« … ») plutôt que coupé net. */
  const FIT_BOXES = ".text, .center, .date-card, .lower-third";
  const FIT_TEXT = ".title, .body, .stat-value, .stat-label, .big-number, .big-label, .role, .bday-name, .date-day, .date-month, .quote-mark";
  const READABLE_SCALE = 0.72;   // en dessous, on préfère abréger le paragraphe que rapetisser encore
  const MIN_SCALE = 0.55;

  // Débordement de la zone, mesuré sur les boîtes de mise en page : les jambages des lettres qui
  // dépassent légèrement d'une ligne serrée (Inter, Verdana…) ne comptent pas comme un débordement.
  function overflows(el) {
    return el.scrollHeight > el.clientHeight + 2 || el.scrollWidth > el.clientWidth + 2;
  }
  function clampLines(el, lines) {
    el.style.display = "-webkit-box";
    el.style.webkitBoxOrient = "vertical";
    el.style.webkitLineClamp = String(lines);
    el.style.overflow = "hidden";
    el.style.lineHeight = "1.18";      // laisse la place aux jambages de la dernière ligne
  }
  function fitBox(box) {
    const items = [...box.querySelectorAll(FIT_TEXT)];
    if (!items.length) return;
    // repart des tailles prévues par la feuille de style (utile quand on réajuste après la police)
    items.forEach((n) => {
      n.style.fontSize = ""; n.style.display = ""; n.style.webkitLineClamp = "";
      n.style.overflow = ""; n.style.lineHeight = "";
    });
    const base = items.map((n) => parseFloat(getComputedStyle(n).fontSize));
    const bad = () => overflows(box);
    let scale = 1;
    const shrinkTo = (limit) => {
      for (let i = 0; i < 20 && bad() && scale > limit; i++) {
        scale = Math.max(limit, scale * 0.94);
        items.forEach((n, k) => { n.style.fontSize = (base[k] * scale).toFixed(1) + "px"; });
      }
    };
    const bodies = [...box.querySelectorAll(".body")];
    const clampBodies = (minLines) => {
      for (const n of bodies) {
        const lh = parseFloat(getComputedStyle(n).lineHeight) || parseFloat(getComputedStyle(n).fontSize) * 1.45;
        for (let lines = Math.round(n.offsetHeight / lh) - 1; lines >= minLines && bad(); lines--) clampLines(n, lines);
      }
    };
    // 1) réduction modérée de tout le texte, qui reste lisible à distance
    shrinkTo(READABLE_SCALE);
    // 2) texte trop long : le paragraphe est abrégé (« … »), au moins 2 lignes gardées
    if (bad()) clampBodies(2);
    // 3) encore trop long : réduction supplémentaire, puis abréviation du paragraphe et du titre
    shrinkTo(MIN_SCALE);
    if (bad()) clampBodies(1);
    for (const n of box.querySelectorAll(".title")) {
      for (let lines = 3; lines >= 1 && bad(); lines--) clampLines(n, lines);
    }
  }
  function shrinkToFit(root) {
    root.querySelectorAll(FIT_BOXES).forEach(fitBox);
  }

  /* Libère les décodeurs vidéo (indispensable sur Raspberry Pi) */
  function releaseVideos(node) {
    node.querySelectorAll("video").forEach((v) => {
      v.pause();
      v.removeAttribute("src");
      v.load();
    });
  }

  /* ------------------------------------------------------------- transitions
     Pour un fondu fluide, même sur Raspberry Pi 3, le changement se fait en deux temps :
     1. préparation (environ 2 s avant le changement, pendant que l'écran est immobile) : la
        diapositive suivante est construite, son texte ajusté, ses images décodées et sa vidéo
        chargée. Elle est posée, quasi transparente, au-dessus de la diapositive en cours : le
        navigateur la dessine dans son propre calque, sans que cela se voie ;
     2. fondu : il ne reste qu'à faire monter son opacité, ce que la carte graphique fait seule. */
  function build(s, i) {
    const node = el("section", `slide slide-${s.type} anim entering`);
    if (s.layout === "left") node.classList.add("layout-left");   // visuel à gauche, texte à droite
    node.style.setProperty("--dur", ((s.duration || 20) + 2) + "s");
    (builders[s.type] || builders.actualite)(s).forEach((c) => node.append(c));
    const theme = nextSlideTheme();
    themeNode(node, theme);
    content.append(node);
    shrinkToFit(node);
    // La police (Inter) peut finir de se charger après le premier affichage : on réajuste alors
    if (document.fonts && document.fonts.status !== "loaded") {
      document.fonts.ready.then(() => { if (node.isConnected) shrinkToFit(node); });
    }
    return { node, i, theme, ready: whenReady(node) };
  }

  // Image suivante dessinée par le navigateur (avec un garde-fou si la page est masquée)
  const nextFrame = () => new Promise((r) => { requestAnimationFrame(() => r()); setTimeout(r, 500); });
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function whenReady(node) {
    const loads = [];
    node.querySelectorAll("img").forEach((img) => { if (img.decode) loads.push(img.decode().catch(() => {})); });
    node.querySelectorAll("video").forEach((v) => {
      if (v.readyState < 2) {
        loads.push(new Promise((r) => {
          v.addEventListener("loadeddata", r, { once: true });
          v.addEventListener("error", r, { once: true });
        }));
      }
    });
    return Promise.race([Promise.all(loads), wait(READY_MAX_MS)])
      .then(nextFrame).then(nextFrame)
      .then(() => (LITE ? wait(250) : null));   // le Pi 3 finit de dessiner le calque
  }

  function discardPrepared() {
    if (!prepared) return;
    releaseVideos(prepared.node);
    prepared.node.remove();
    if (themeMode === "alternate") shown--;      // l'alternance sombre / clair reste régulière
    prepared = null;
  }

  // Prépare la diapositive d'indice i (ou réutilise celle déjà prête)
  function prepare(i) {
    if (prepared && prepared.i === i && prepared.node.isConnected) return prepared;
    discardPrepared();
    prepared = build(slides[i], i);
    return prepared;
  }

  function upcomingIndex() {
    let i = idx;
    do { i = (i + 1) % slides.length; } while (!playable(slides[i]));
    return i;
  }

  // Préparation anticipée (sauf si une nouvelle playlist doit s'appliquer au prochain changement)
  function schedulePrep(inMs) {
    clearTimeout(prepTimer);
    prepTimer = setTimeout(() => {
      if (!pending && slides.some(playable)) prepare(upcomingIndex());
    }, Math.max(0, inMs));
  }

  function show(p) {
    const node = p.node;
    stage.classList.toggle("theme-light", p.theme === "light");   // en-tête et pied de page
    const olds = [...content.querySelectorAll(".slide")].filter((o) => o !== node);
    olds.forEach((o) => o.querySelectorAll("video").forEach((v) => v.pause()));
    node.classList.remove("entering");
    node.classList.add("arriving");
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      node.classList.remove("arriving");
      olds.forEach((o) => { releaseVideos(o); o.remove(); });
    };
    node.addEventListener("animationend", (e) => { if (e.target === node) finish(); });
    setTimeout(finish, FADE_MS + 200);
    // Lecture des vidéos : si le son est refusé par le navigateur, on repasse en muet
    node.querySelectorAll("video").forEach((v) => {
      const pr = v.play();
      if (pr && pr.catch) pr.catch(() => { v.muted = true; v.play().catch(() => {}); });
    });
  }

  function renderIdle() {
    discardPrepared();
    content.querySelectorAll(".slide").forEach((o) => { releaseVideos(o); o.remove(); });
    const d = el("section", "slide");
    setSurface(themeMode === "alternate" ? "dark" : themeMode, d);
    d.append(...bg());
    const box = el("div", "idle");
    const img = el("img");
    img.src = stage.classList.contains("theme-light") ? "/static/img/axians-logo.svg" : "/static/img/axians-logo-white.svg";
    img.alt = "Axians";
    box.append(img, el("p", "", "Aucun contenu programmé pour le moment"));
    d.append(box);
    content.append(d);
    current = d;
  }

  /* ------------------------------------------------------ indicateur de progression */
  function buildSegments() {
    const box = $("segments");
    box.innerHTML = "";
    slides.forEach(() => { const s = el("div", "seg"); s.append(el("i")); box.append(s); });
  }
  function startSegment(ms) {
    const segs = $("segments").children;
    // Les diapositives sautées aujourd'hui (anniversaires sans personne fêtée) n'ont pas de segment
    $("segments").hidden = slides.filter(playable).length < 2;
    // La barre avance par transformation (scaleX) et non par sa largeur : la carte graphique
    // l'anime seule, sans recalcul de la page à chaque image (important sur Raspberry Pi)
    [...segs].forEach((s, i) => {
      s.hidden = !playable(slides[i]);
      s.classList.toggle("done", i < idx);
      const bar = s.firstChild;
      bar.style.transition = "none";
      bar.style.transform = i < idx ? "scaleX(1)" : "scaleX(0)";
    });
    const bar = segs[idx] && segs[idx].firstChild;
    if (!bar || !ms) return;
    void bar.offsetWidth;
    bar.style.transition = `transform ${ms}ms linear`;
    bar.style.transform = "scaleX(1)";
  }

  /* -------------------------------------------------------------- bandeau */
  let tickerSig = "";
  function updateTicker(items) {
    const sig = JSON.stringify(items || []);
    if (sig === tickerSig) return;
    tickerSig = sig;
    const box = $("ticker"), track = $("ticker-track");
    track.innerHTML = "";
    if (!items || !items.length) { box.hidden = true; return; }
    items.forEach((t, i) => {
      if (i) track.append(el("span", "sep"));
      track.append(document.createTextNode(t));
    });
    box.hidden = false;
    const chars = items.join("   ").length;
    track.style.setProperty("--ticker-dur", Math.max(20, chars * 0.22) + "s");
  }

  /* ----------------------------------------------------------- lecture */
  function preload(s) {
    if (s && s.media && s.media_type === "image") { const i = new Image(); i.src = s.media; }
  }

  function commitPending() {
    if (!pending) return;
    slides = pending.slides || [];
    settings = pending.settings || {};
    version = pending.version;
    pending = null;
    idx = -1;
    stage.classList.toggle("no-clock", settings.show_clock === false);
    updateTicker(settings.ticker);
    buildSegments();
  }

  async function next() {
    clearTimeout(timer);
    clearTimeout(prepTimer);
    const token = ++seq;
    if (pending) { discardPrepared(); commitPending(); }
    if (!slides.some(playable)) {
      renderIdle();
      $("segments").hidden = true;
      timer = setTimeout(next, 10000);
      return;
    }
    // Diapositive suivante : déjà prête le plus souvent (préparée pendant la précédente)
    const p = prepare(upcomingIndex());
    await p.ready;
    if (token !== seq || prepared !== p) return;   // une autre demande a pris le relais entre-temps
    prepared = null;
    idx = p.i;
    const s = slides[idx];
    current = p.node;
    show(p);
    preload(slides.find((x, i) => i > idx && playable(x)) || slides.find(playable));

    const v = s.type === "video" ? current.querySelector("video.full") : null;
    if (v && !s.duration) {
      // Vidéo lue jusqu'au bout : la durée réelle pilote la progression
      const node = current;
      const done = () => { if (current === node) next(); };
      const onMeta = () => {
        if (!isFinite(v.duration)) return;
        const left = Math.max(0, (v.duration - v.currentTime) * 1000);
        startSegment(left);
        schedulePrep(left - PREP_LEAD_MS);
      };
      startSegment(0);
      if (v.readyState >= 1) onMeta();          // durée déjà connue (vidéo chargée à l'avance)
      else v.addEventListener("loadedmetadata", onMeta, { once: true });
      v.addEventListener("ended", done, { once: true });
      v.addEventListener("error", () => setTimeout(done, 1500), { once: true });
      timer = setTimeout(done, MAX_VIDEO_MS);
      return;
    }
    const ms = Math.max(4, s.duration || 12) * 1000;
    startSegment(ms);
    timer = setTimeout(next, ms);
    schedulePrep(ms - PREP_LEAD_MS);
  }

  function accept(data) {
    if (!data || data.version === version) return;
    applyTheme(data.settings && data.settings.theme);   // changement de thème appliqué tout de suite
    const idle = !slides.length;
    pending = data;
    if (idle) next();   // premier chargement ou écran d'attente : démarrage immédiat
    // sinon : nouvelle playlist appliquée à la fin de la diapositive en cours
  }

  async function load() {
    const q = new URLSearchParams();
    if (SCREEN) q.set("screen", SCREEN);
    if (PREVIEW) q.set("preview", PREVIEW);
    try {
      const r = await fetch("/api/playlist?" + q, { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      $("offline").hidden = true;
      if (!PREVIEW) try { localStorage.setItem(CACHE_KEY, JSON.stringify(data)); } catch (_) {}
      accept(data);
    } catch (e) {
      $("offline").hidden = false;
      if (version === null) {
        const cached = localStorage.getItem(CACHE_KEY);
        if (cached) accept(JSON.parse(cached));
        else if (!timer) next();
      }
    }
  }

  // Flèche droite / espace : diapositive suivante
  addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight" || e.key === " ") next();
  });

  load();
  setInterval(load, POLL_MS);
})();

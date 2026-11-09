/* Player d'affichage dynamique Axians.
 * Paramètres d'URL :
 *   screen=<nom>   n'affiche que les contenus destinés à cet écran (+ ceux destinés à tous)
 *   preview=<id>   aperçu d'une diapositive (nécessite d'être connecté à l'admin)
 *   lite=1         désactive les animations (Raspberry Pi 3 et antérieurs)
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
  if (params.get("lite") === "1") document.body.classList.add("lite");

  /* ------------------------------------------------------------------ thèmes */
  let themeMode = "dark";   // "dark" | "light" | "alternate" (une diapositive sur deux)
  let shown = 0;            // nombre de diapositives affichées : sert à l'alternance

  function setSurface(t, node) {
    // node : diapositive à habiller ; l'en-tête et le pied suivent via la classe de .stage
    stage.classList.toggle("theme-light", t === "light");
    if (node) {
      node.classList.toggle("theme-light", t === "light");
      node.classList.toggle("theme-dark", t !== "light");
    }
  }
  function nextSlideTheme() {
    if (themeMode !== "alternate") return themeMode;
    return shown++ % 2 === 0 ? "dark" : "light";
  }
  function applyTheme(theme) {
    themeMode = FORCED_THEME || (THEME_MODES.includes(theme) ? theme : "dark");
    // Sans alternance, on rhabille immédiatement la diapositive en cours
    if (themeMode !== "alternate") {
      setSurface(themeMode, content.querySelector(".slide:not(.leaving)"));
    }
  }
  applyTheme("");

  let slides = [], settings = {}, version = null, pending = null;
  let idx = -1, timer = null, current = null;

  /* --------------------------------------------------------- mise à l'échelle */
  function fit() {
    // Taille réellement visible (certains moteurs web Android la donnent mieux par documentElement)
    const w = innerWidth || document.documentElement.clientWidth;
    const h = innerHeight || document.documentElement.clientHeight;
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
    v.autoplay = true;
    v.preload = "auto";
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

  function render(s) {
    const node = el("section", `slide slide-${s.type} anim`);
    if (s.layout === "left") node.classList.add("layout-left");   // visuel à gauche, texte à droite
    node.style.setProperty("--dur", ((s.duration || 20) + 2) + "s");
    (builders[s.type] || builders.actualite)(s).forEach((c) => node.append(c));
    setSurface(nextSlideTheme(), node);
    content.querySelectorAll(".slide").forEach((old) => {
      old.classList.add("leaving");
      old.querySelectorAll("video").forEach((v) => v.pause());
      setTimeout(() => { releaseVideos(old); old.remove(); }, 850);
    });
    content.append(node);
    shrinkToFit(node);
    // La police (Inter) peut finir de se charger après le premier affichage : on réajuste alors
    if (document.fonts && document.fonts.status !== "loaded") {
      document.fonts.ready.then(() => { if (node.isConnected) shrinkToFit(node); });
    }
    // Lecture des vidéos : si le son est refusé par le navigateur, on repasse en muet
    node.querySelectorAll("video").forEach((v) => {
      const p = v.play();
      if (p && p.catch) p.catch(() => { v.muted = true; v.play().catch(() => {}); });
    });
    return node;
  }

  function renderIdle() {
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
    [...segs].forEach((s, i) => {
      s.hidden = !playable(slides[i]);
      s.classList.toggle("done", i < idx);
      const bar = s.firstChild;
      bar.style.transition = "none";
      bar.style.width = i < idx ? "100%" : "0";
    });
    const bar = segs[idx] && segs[idx].firstChild;
    if (!bar || !ms) return;
    void bar.offsetWidth;
    bar.style.transition = `width ${ms}ms linear`;
    bar.style.width = "100%";
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

  function next() {
    clearTimeout(timer);
    commitPending();
    if (!slides.some(playable)) {
      renderIdle();
      $("segments").hidden = true;
      timer = setTimeout(next, 10000);
      return;
    }
    do { idx = (idx + 1) % slides.length; } while (!playable(slides[idx]));
    const s = slides[idx];
    current = render(s);
    preload(slides.find((x, i) => i > idx && playable(x)) || slides.find(playable));

    const v = s.type === "video" ? current.querySelector("video.full") : null;
    if (v && !s.duration) {
      // Vidéo lue jusqu'au bout : la durée réelle pilote la progression
      const node = current;
      const done = () => { if (current === node) next(); };
      v.addEventListener("loadedmetadata", () => {
        if (isFinite(v.duration)) startSegment(Math.max(0, (v.duration - v.currentTime) * 1000));
      }, { once: true });
      v.addEventListener("ended", done, { once: true });
      v.addEventListener("error", () => setTimeout(done, 1500), { once: true });
      startSegment(0);
      timer = setTimeout(done, MAX_VIDEO_MS);
      return;
    }
    const ms = Math.max(4, s.duration || 12) * 1000;
    startSegment(ms);
    timer = setTimeout(next, ms);
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

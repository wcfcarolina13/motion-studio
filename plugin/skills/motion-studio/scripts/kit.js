// motion-studio kit: the engine every film is built on. Copied into each film folder by `studio.py new`.
// Rule of the engine: every style is a pure function of t. No CSS transitions, timers, or state between frames,
// so any frame can be rendered alone (stills, subframe motion blur, scrubbing in the preview).
(function () {
  const VERSION = "1.0.0";
  const NS = "http://www.w3.org/2000/svg";

  // frame + caption band per format; scenes are clipped above `band` so nothing crosses a caption
  const FORMATS = {
    "4x5": { w: 1080, h: 1350, capTop: 1150, band: 1098, capSize: 50 },
    "9x16": { w: 1080, h: 1920, capTop: 1560, band: 1500, capSize: 54 },
    "16x9": { w: 1920, h: 1080, capTop: 930, band: 900, capSize: 48 },
  };
  const HOUSE = { BG: "#0b0b0b", INK: "#e8e6e1", GREY: "#969691", ACCENT: "#e8772e", PANEL: "#141414" };

  const K = { VERSION, FORMATS };
  window.K = K;

  // ── math ───────────────────────────────────────────────
  K.clamp01 = x => Math.max(0, Math.min(1, x));
  K.lerp = (a, c, p) => a + (c - a) * p;
  K.prog = (t, a, c) => K.clamp01((t - a) / (c - a));
  K.eio = x => x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
  K.eout = x => 1 - Math.pow(1 - x, 3);
  K.ein = x => x * x * x;
  K.smooth = x => x * x * (3 - 2 * x);
  // closed-form spring step 0 → 1; z < 1 overshoots. Once the ringing can no longer exceed 0.15 % it is exactly 1,
  // so a settled item (and its text) stops moving instead of shifting by fractions of a pixel for seconds
  K.step = (t, t0, w = 14, z = 0.62) => {
    if (t <= t0) return 0;
    const d = t - t0, wd = w * Math.sqrt(1 - z * z), env = Math.exp(-z * w * d);
    if (env * (1 + z * w / wd) < 0.0015) return 1;
    return 1 - env * (Math.cos(wd * d) + (z * w / wd) * Math.sin(wd * d));
  };
  // keyframes [[t, v], ...], eased between neighbours; kfLog interpolates in log space (use it for zoom)
  K.kf = (t, keys, ease = K.eio) => {
    if (t <= keys[0][0]) return keys[0][1];
    for (let i = 1; i < keys.length; i++) if (t <= keys[i][0]) {
      const [t0, v0] = keys[i - 1], [t1, v1] = keys[i];
      return K.lerp(v0, v1, ease(K.prog(t, t0, t1)));
    }
    return keys[keys.length - 1][1];
  };
  K.kfLog = (t, keys) => Math.exp(K.kf(t, keys.map(([k, v]) => [k, Math.log(v)])));
  // a decaying pulse after an event (hits, landings)
  K.bump = (t, t0, k = 9) => t < t0 ? 0 : Math.exp(-(t - t0) * k) * Math.sin(Math.min(Math.PI, (t - t0) * 18));
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  K.mix = (a, c, p) => { const A = hex(a), C = hex(c); return "rgb(" + A.map((v, i) => Math.round(K.lerp(v, C[i], p))).join(",") + ")"; };
  // seeded random, so layouts are identical on every render
  K.rng = (seed = 7) => () => (seed = (seed * 16807) % 2147483647) / 2147483647;

  // ── setup: format, beat grid, palette, stage ───────────
  K.setup = ({ format = "4x5", bpm = 120, bars = 16, palette = {}, title = "" } = {}) => {
    const F = FORMATS[format];
    if (!F) throw new Error(`unknown format ${format}: use one of ${Object.keys(FORMATS).join(", ")}`);
    Object.assign(K, { FORMAT: format, W: F.w, H: F.h, CAP_TOP: F.capTop, BAND: F.band, CAP_SIZE: F.capSize });
    K.C = Object.assign({}, HOUSE, palette);
    K.BPM = bpm; K.BEAT = 60 / bpm; K.BAR = 4 * K.BEAT;
    K.b = (bar, beat = 0) => (bar * 4 + beat) * K.BEAT;
    K.DUR = K.b(bars);
    window.STUDIO = { kit: VERSION, title, bg: K.C.BG, format: { name: format, w: F.w, h: F.h, capTop: F.capTop, band: F.band }, bpm, bars, scenes: [], text: [], warnings: [] };
    const stage = document.getElementById("stage"), svg = document.getElementById("svg");
    stage.style.width = F.w + "px"; stage.style.height = F.h + "px"; stage.style.background = K.C.BG;
    document.body.style.background = K.C.BG;
    svg.setAttribute("width", F.w); svg.setAttribute("height", F.h); svg.setAttribute("viewBox", `0 0 ${F.w} ${F.h}`);
    K.svg = svg; K.defs = svg.querySelector("defs"); K.top = document.getElementById("top");
    // the caption band clip: put scene groups inside K.banded() so they stop above the captions
    K.clipRect("band", -10, -10, F.w + 20, F.band + 10);
    return K;
  };
  K.scene = (name, fromBar, toBar) => { window.STUDIO.scenes.push({ name, from: K.b(fromBar), to: K.b(toBar), bars: [fromBar, toBar] }); };
  // register in-scene text that is not a caption, so the blind read and the preview know about it
  K.label = (text, t0, t1, where = "in scene") => { window.STUDIO.text.push({ t0, t1, where, text }); };

  // ── dom ────────────────────────────────────────────────
  K.el = (tag, attrs = {}, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    (parent || K.top).appendChild(e);
    return e;
  };
  K.set = (e, attrs) => { for (const k in attrs) e.setAttribute(k, attrs[k]); };
  K.show = (e, on) => { e.style.display = on ? "" : "none"; };
  K.rect = (x, y, w, h, rx = 0) => { w = Math.max(0, w); h = Math.max(0, h); return { x, y, width: w, height: h, rx: Math.min(rx, w / 2, h / 2) }; };
  K.text = (str, attrs, parent) => { const e = K.el("text", Object.assign({ "font-family": "Georgia, serif", fill: K.C.INK }, attrs), parent); e.textContent = str; return e; };
  // standard symbols, drawn here on a 24-unit grid as filled shapes: use these rather than an improvised mark, which a
  // phone viewer can't read (a lone chevron for "share" was unreadable on an end screen, 2026-11-03). K.icon(name,
  // { x, y, size, fill }, parent) draws one with its top-left at x, y, size px square, marked data-icon for check's
  // legibility test (at least 18 px when the frame is shown 360 px wide, about 96 px on a 1920 frame)
  K.ICONS = {
    share: "M14 5 L22 12 L14 19 V15 C8.5 15 4.8 16.8 2 21 C3 15 6.5 10 14 9 Z",                       // a curved arrow pointing right
    play: "M8 5 L19 12 L8 19 Z",
    bell: "M12 2.5 C8.4 2.5 6 5.1 6 8.6 V13.5 L4 16.5 V17.5 H20 V16.5 L18 13.5 V8.6 C18 5.1 15.6 2.5 12 2.5 Z M9.5 19 C9.8 20.5 10.8 21.5 12 21.5 C13.2 21.5 14.2 20.5 14.5 19 Z",
    like: "M2 10 H6 V21 H2 Z M8 21 V10 L12.5 3 C13.8 3 14.8 4 14.6 5.3 L14 9 H20 C21.2 9 22.1 10.1 21.9 11.3 L20.6 19.3 C20.4 20.3 19.5 21 18.5 21 Z",
    comment: "M3 4 H21 V16 H9 L4 21 V16 H3 Z",
    next: "M3 10.5 H15.5 L10.5 5.5 L12.6 3.4 L21.2 12 L12.6 20.6 L10.5 18.5 L15.5 13.5 H3 Z",     // an arrow pointing right
  };
  K.icon = (name, { x = 0, y = 0, size = 96, fill } = {}, parent) => {
    if (!K.ICONS[name]) throw new Error(`K.icon: unknown icon "${name}" (${Object.keys(K.ICONS).join(", ")})`);
    const g = K.el("g", { "data-icon": name, transform: `translate(${x} ${y}) scale(${size / 24})` }, parent);
    K.el("path", { d: K.ICONS[name], fill: fill || K.C.INK, "fill-rule": "evenodd" }, g);
    return g;
  };
  K.group = (id, parent) => K.el("g", id ? { id } : {}, parent || K.svg.querySelector("#world") || K.svg);
  K.banded = id => { const g = K.group(id); g.setAttribute("clip-path", "url(#band)"); return g; };
  // camera on a group: world point (cx, cy) at frame centre, zoom z (drive z with K.kfLog)
  K.cam = (g, cx, cy, z) => g.setAttribute("transform", `translate(${K.W / 2} ${K.H / 2}) scale(${z}) translate(${-cx} ${-cy})`);
  K.toScreen = (v, x, y) => [(x - v.cx) * v.z + K.W / 2, (y - v.cy) * v.z + K.H / 2];
  // clip paths you animate each frame: set their attributes in render(t)
  K.clipRect = (id, x = 0, y = 0, w = 0, h = 0) => K.el("rect", { x, y, width: w, height: h }, K.el("clipPath", { id }, K.defs));
  K.clipCircle = (id) => K.el("circle", { r: 0 }, K.el("clipPath", { id }, K.defs));
  K.cursor = parent => {
    const g = K.el("g", {}, parent);
    K.el("path", { d: "M 0 0 L 0 34 L 8 26 L 14 40 L 20 37 L 14 24 L 25 24 Z", fill: "#ffffff", stroke: "#000", "stroke-width": 2, "stroke-linejoin": "round" }, g);
    return g;
  };

  // ── captions: whole phrases rise out of one mask line and leave through it ──
  // parts: [["text"], ["accent words", K.C.ACCENT], ["italic", color, true]]
  K._caps = [];
  K.caption = (parts, tIn, tOut = null, { top = K.CAP_TOP, size = K.CAP_SIZE, where = "caption" } = {}) => {
    const d = document.createElement("div");
    const lh = Math.round(size * 1.35);
    Object.assign(d.style, { position: "absolute", left: "60px", width: (K.W - 120) + "px", top: top + "px", height: lh + "px",
      overflow: "hidden", textAlign: "center", fontFamily: "Georgia, serif", fontSize: size + "px", color: K.C.INK });
    const inner = document.createElement("div");
    inner.style.lineHeight = lh + "px";
    parts.forEach(([s, color, italic]) => {
      const sp = document.createElement("span");
      sp.textContent = s; sp.style.whiteSpace = "pre"; sp.style.color = color || K.C.INK; if (italic) sp.style.fontStyle = "italic";
      inner.appendChild(sp);
    });
    d.appendChild(inner);
    document.getElementById("caps").appendChild(d);
    window.STUDIO.text.push({ t0: tIn, t1: tOut, where, text: parts.map(p => p[0]).join("") });
    K._caps.push(t => {
      const up = K.eout(K.prog(t, tIn, tIn + 0.26)), gone = tOut == null ? 0 : K.ein(K.prog(t, tOut, tOut + 0.22));
      inner.style.transform = `translateY(${(1 - up) * 110 - gone * 110}%)`;
      d.style.visibility = t < tIn || (tOut != null && t > tOut + 0.22) ? "hidden" : "visible";
    });
    return d;
  };

  // ── run: wire render(t) to window.seek; captions update after the scene ──
  K.run = render => {
    const seek = t => { const tt = Math.min(Math.max(t, 0), K.DUR - 1e-6); render(tt); K._caps.forEach(c => c(tt)); return true; };
    window.seek = seek;
    window.DUR = K.DUR;
    window.STUDIO.text.sort((a, b) => a.t0 - b.t0);
    // lint: a caption wider than the frame gets clipped by its own mask; warn instead of shipping it
    // (added to what the page found while building: said(), a close-up's lenses)
    document.querySelectorAll("#caps > div").forEach(d => {
      const inner = d.firstChild, over = inner.scrollWidth - d.clientWidth;
      if (over > 1) window.STUDIO.warnings.push(`caption too wide by ${over}px: "${inner.textContent}"`);
    });
    seek(0);
  };
})();

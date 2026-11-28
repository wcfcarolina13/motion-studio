// motion-studio house style: the collage look the map scene draws in (references/maps.md), in one place so the rest of a
// long-form film can draw in it too. With "style": "collage" in script.json, panel callouts become paper tags pinned to
// the picture, a still can carry marker ink and a paint sweep (data.ink), and the HUD, chapter tag, source line and charts
// take the same paper, tape, inks and typefaces (longform.md, "Collage style"). Copied into each film by `longform.py init`.
// Every value is a pure function of t. Overlay motion is on twos and paper pieces boil, as on the map.
(function () {
  const PAL = { board: "#EFEBE3", ink: "#1D1C20", yellow: "#EDBD2E", beige: "#F1EDE4", tape: "#E8DDB5", red: "#B3261E" };
  const FONTS = { caslon: "MS Caslon", caslonI: "MS Caslon Italic", marker: "MS Marker", elite: "MS Elite", anton: "MS Anton", nunito: "MS Nunito" };
  const FILES = { caslon: "LibreCaslonText.ttf", caslonI: "LibreCaslonText-Italic.ttf", marker: "PermanentMarker-Regular.ttf",
                  elite: "SpecialElite-Regular.ttf", anton: "Anton-Regular.ttf", nunito: "Nunito.ttf" };
  // font-family values with a fallback, for attributes
  const FAM = { caslon: `"${FONTS.caslon}", Georgia, serif`, caslonI: `"${FONTS.caslonI}", Georgia, serif`,
                marker: `"${FONTS.marker}", "Comic Sans MS", cursive`, elite: `"${FONTS.elite}", "Courier New", monospace`,
                anton: `"${FONTS.anton}", Impact, sans-serif`, nunito: `"${FONTS.nunito}", "Arial Rounded MT Bold", Arial, sans-serif` };

  // the fonts are read from the film's fonts/ folder (mapart.py fonts, or timeline for a collage film) and added before any
  // text is laid out: an @font-face loads late, and the engine measures text once at build time, so cards sized with the
  // fallback font came out the wrong width. A file read synchronously becomes a loaded FontFace at once.
  // keys: the faces this film needs (default: the collage and map set); only those are warned about when missing
  let installed = null;
  const BASE = ["caslon", "caslonI", "marker", "elite", "anton"];
  function installFonts(keys) {
    installed = installed || { missing: [] };
    const miss = [];
    (keys || BASE).filter(k => !(k in installed)).forEach(k => {
      try {
        const x = new XMLHttpRequest();
        x.open("GET", "fonts/" + FILES[k], false);
        x.overrideMimeType("text/plain; charset=x-user-defined");
        x.send();
        const s = x.responseText;
        if ((x.status && x.status !== 200) || !s || s.length < 1000) throw new Error("empty");
        const b = new Uint8Array(s.length);
        for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i) & 255;
        const ff = new FontFace(FONTS[k], b.buffer, k === "caslon" || k === "caslonI" || k === "nunito" ? { weight: "100 1000" } : {});
        document.fonts.add(ff);
        installed[k] = true;
      } catch (e) { installed[k] = false; installed.missing.push(FILES[k]); miss.push(FILES[k]); }
    });
    if (miss.length && window.STUDIO) (window.STUDIO.warnings = window.STUDIO.warnings || [])
      .push(`house style: fonts missing (${miss.join(", ")}): run mapart.py fonts <film>`);
    return installed;
  }

  // ── timing: frames, twos, the boil ──
  // the renderer seeks to t rounded to 6 decimals, so frame n can arrive as n/30 - 5e-7 s: a thousandth of a frame covers
  // the rounding (a 1e-6 margin put every third frame on the frame before; 2026-11-25)
  function clock(fps) {
    const frameOf = t => Math.floor(t * fps + 1e-3);
    return {
      fps, frameOf,
      twos: t => Math.floor(frameOf(t) / 2) * 2 / fps,                  // motion steps on twos
      boilSeed: (t, id) => Math.floor(frameOf(t) / 8) % 3 + id * 7,      // a new jitter every 8 frames, three in turn
    };
  }
  const slap = f => f < 0 ? 0 : f < 2 ? 1.1 : f < 4 ? 0.98 : 1;          // arrival: 1.1, 0.98, 1.0 on twos, never a fade
  const hash = (...v) => { let h = 2166136261; v.forEach(x => { h ^= Math.round(x * 1000) | 0; h = Math.imul(h, 16777619); }); return ((h >>> 0) % 100000) / 100000; };
  const sm = x => x * x * (3 - 2 * x);

  // ── marker ink: variable-width strokes drawn as filled outlines ──
  const noise1 = (u, seed) => Math.sin(u * 0.11 + seed * 9.1) * 0.6 + Math.sin(u * 0.27 + seed * 3.7) * 0.3 + Math.sin(u * 0.53 + seed * 1.3) * 0.1;
  function resample(pts, step = 1.6) {
    const out = [pts[0]], cum = [0];
    for (let i = 1; i < pts.length; i++) {
      const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], d = Math.hypot(x1 - x0, y1 - y0);
      const acc = cum[cum.length - 1], n = Math.max(1, Math.ceil(d / step));
      for (let k = 1; k <= n; k++) { out.push([x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n]); cum.push(acc + d * k / n); }
    }
    return { pts: out, cum };
  }
  // one stroke: pts (screen), width w, the first `upto` px of it, tapered ends; returns path data
  function strokeD(pts, w, upto, seed, taper = 9) {
    if (pts.length < 2) return "";
    const { pts: q, cum } = resample(pts);
    let m = q.length; while (m > 1 && cum[m - 1] > upto) m--;
    if (m < 2) return "";
    const L = cum[m - 1], left = [], right = [];
    for (let i = 0; i < m; i++) {
      const a = q[Math.max(0, i - 1)], b = q[Math.min(m - 1, i + 1)];
      let nx = -(b[1] - a[1]), ny = b[0] - a[0];
      const nl = Math.hypot(nx, ny) || 1; nx /= nl; ny /= nl;
      const s = cum[i], tp = Math.min(1, 0.35 + 0.65 * Math.min(s, L - s) / taper);
      const wi = w * (1 + 0.15 * noise1(s, seed)) * tp / 2;
      left.push([q[i][0] + nx * wi, q[i][1] + ny * wi]); right.push([q[i][0] - nx * wi, q[i][1] - ny * wi]);
    }
    return "M" + left.concat(right.reverse()).map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L") + "Z";
  }
  // a dry crayon pass along the same line, a third as wide, with gaps: drawn in the same ink, so it only adds
  function crayonD(pts, w, upto, seed) {
    const { pts: q, cum } = resample(pts, 2.5);
    let d = "", on = false;
    for (let i = 0; i < q.length && cum[i] <= upto; i++) {
      const keep = hash(seed, Math.floor(cum[i] / 6)) > 0.35;
      const o = (hash(seed + 1, Math.floor(cum[i] / 14)) - 0.5) * w * 0.5;
      const p = [q[i][0] + o * 0.3, q[i][1] + o];
      if (keep) { d += (on ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1); on = true; } else on = false;
    }
    return d;
  }
  const quad = (a, b, bend, n = 48) => {
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2, dx = b[0] - a[0], dy = b[1] - a[1];
    const c = [mx - dy * bend, my + dx * bend];
    return [...Array(n + 1)].map((_, i) => { const u = i / n; return [(1 - u) * (1 - u) * a[0] + 2 * (1 - u) * u * c[0] + u * u * b[0], (1 - u) * (1 - u) * a[1] + 2 * (1 - u) * u * c[1] + u * u * b[1]]; });
  };
  const dashes = (pts, on, off) => {      // split a polyline into dash pieces
    const { pts: q, cum } = resample(pts, 2), out = [];
    let cur = [];
    q.forEach((p, i) => { if ((cum[i] % (on + off)) < on) cur.push(p); else if (cur.length) { out.push(cur); cur = []; } });
    if (cur.length) out.push(cur);
    return out;
  };
  const jitter = (pts, seed, a = 0.8) => pts.map((p, i) => [p[0] + (hash(seed, i, 1) - 0.5) * 2 * a, p[1] + (hash(seed, i, 2) - 0.5) * 2 * a]);
  // a hand-drawn loop around (x, y): a little over one turn, slightly wider as it goes, flattened like a quick circle
  function loopPts(x, y, rx, ry, n = 60) {
    const pts = [];
    for (let i = 0; i <= n; i++) { const u = i / n, a = -1.75 + u * 2 * Math.PI * 1.08, k = 1 + 0.06 * u; pts.push([x + Math.cos(a) * rx * k, y + Math.sin(a) * ry * k]); }
    return pts;
  }
  // an arrowhead: two strokes back from the tip at +-0.48 rad
  function headD(pts, w, seed, hp, len = 26) {
    const e = pts[pts.length - 1], f = pts[pts.length - 6] || pts[0], ang = Math.atan2(e[1] - f[1], e[0] - f[0]);
    return [-0.48, 0.48].map((da, i) => strokeD(jitter([e, [e[0] - len * Math.cos(ang + da), e[1] - len * Math.sin(ang + da)]], seed + 5 + i), w, len * hp, seed + 5 + i, 4)).join("");
  }
  // torn-paper edge: a rectangle whose edges wander by up to a px or two, fixed per seed (paper doesn't boil its shape)
  function paperD(x, y, w, h, seed, rough = 1.6) {
    const pts = [], side = (ax, ay, bx, by, k) => {
      const n = Math.max(2, Math.round(Math.hypot(bx - ax, by - ay) / 18));
      for (let i = 0; i < n; i++) { const u = i / n; pts.push([ax + (bx - ax) * u + (hash(seed, k, i, 1) - 0.5) * rough * (ay === by ? 0.4 : 2), ay + (by - ay) * u + (hash(seed, k, i, 2) - 0.5) * rough * (ax === bx ? 0.4 : 2)]); }
    };
    side(x, y, x + w, y, 1); side(x + w, y, x + w, y + h, 2); side(x + w, y + h, x, y + h, 3); side(x, y + h, x, y, 4);
    return "M" + pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L") + "Z";
  }
  // a strip of tape across a corner or an edge: translucent, ends cut ragged
  function tapeD(cx, cy, w, h, rot, seed) {
    const c = Math.cos(rot), s = Math.sin(rot), pts = [];
    const n = 5, P = (u, v) => [cx + u * c - v * s, cy + u * s + v * c];
    for (let i = 0; i <= n; i++) pts.push(P(-w / 2 + (hash(seed, i, 3) - 0.5) * 4, -h / 2 + h * i / n));
    for (let i = n; i >= 0; i--) pts.push(P(w / 2 + (hash(seed, i, 4) - 0.5) * 4, -h / 2 + h * i / n));
    return "M" + pts.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L") + "Z";
  }

  window.House = { PAL, FONTS, FILES, FAM, installFonts, clock, slap, hash, sm, noise1, resample, strokeD, crayonD, quad, dashes,
                   jitter, loopPts, headD, paperD, tapeD };
})();

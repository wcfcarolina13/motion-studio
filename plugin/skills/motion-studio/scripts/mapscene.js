// motion-studio map scene: one engraved paper map for a whole run of segments, a camera that flies across it, and
// collage pieces pinned to places (references/maps.md). Copied into each long-form film by `longform.py init`.
// The map itself is drawn once by mapart.py (maps/map.json and its images); this draws everything that moves.
// Every value is a pure function of t, like the rest of the engine. Motion is on twos (15 steps a second at 30 fps),
// and every paper piece "boils" (a small jitter, a new one every 8 frames), so no frame is ever still.
(function () {
  // "yellow" is the film's accent: palette ACCENT (mapart.py paints regions with the same colour), Vox yellow by default
  const PAL = { board: "#EFEBE3", ink: "#1D1C20", yellow: "#EDBD2E", beige: "#F1EDE4" };
  const FONTS = { caslon: "MS Caslon", caslonI: "MS Caslon Italic", marker: "MS Marker", elite: "MS Elite", anton: "MS Anton" };
  const FILES = { caslon: "LibreCaslonText.ttf", caslonI: "LibreCaslonText-Italic.ttf", marker: "PermanentMarker-Regular.ttf",
                  elite: "SpecialElite-Regular.ttf", anton: "Anton-Regular.ttf" };

  window.MapScene = function (E) {
    const { K, W, H, READ, VERT, SAFE, SEGS, TL, sceneG, cueT } = E;
    const FPS = TL.fps || 30, M = TL.map;
    PAL.yellow = (TL.palette || {}).ACCENT || "#EDBD2E";
    const segs = SEGS.filter(s => s.type === "map");
    const warn = m => window.STUDIO.warnings.push("map: " + m);
    if (!segs.length) return null;
    if (!M) { warn("no maps/map.json in the timeline: run mapart.py build, then timeline"); }

    // fonts: shipped in the film's fonts/ folder by mapart.py (OFL and Apache-2.0)
    const css = document.createElement("style");
    css.textContent = Object.keys(FILES).map(k => `@font-face { font-family: "${FONTS[k]}"; src: url("fonts/${FILES[k]}"); font-weight: 100 900; }`).join("\n");
    document.head.appendChild(css);

    // ── small helpers ──
    const el = (tag, a, p) => K.el(tag, a, p);
    // the renderer seeks to t rounded to 6 decimals, so frame n can arrive as n/30 - 5e-7 s, which is n - 1.5e-5 frames:
    // a 1e-6 margin put every third frame (n % 3 == 1) on the frame before, so moves on twos stepped 2-2-1-3 and the
    // boil landed a frame late (2026-11-25). A thousandth of a frame covers the rounding and is far from the next frame.
    const frameOf = t => Math.floor(t * FPS + 1e-3);
    const twos = t => Math.floor(frameOf(t) / 2) * 2 / FPS;              // motion steps on twos
    const hash = (...v) => { let h = 2166136261; v.forEach(x => { h ^= Math.round(x * 1000) | 0; h = Math.imul(h, 16777619); }); return ((h >>> 0) % 100000) / 100000; };
    const sm = x => x * x * (3 - 2 * x);
    const P = M ? M.proj : { lon0: 0, lat0: 0, lon1: 1, lat1: 1, k: 1, s: 1, w: W, h: H };
    const toMap = (lon, lat) => [(lon - P.lon0) * P.k * P.s, (P.lat1 - lat) * P.s];
    const ll = v => Array.isArray(v) ? v : [v.lon, v.lat];
    const tOf = (x, fb) => (x && typeof x === "object" && x.t != null) ? x.t - 0.05 : fb;
    // "until": {"at": "<word in this segment>"}, or a later segment's id ("s07"), which ends the item as that segment starts
    const untilT = (u, fb) => { if (typeof u === "string") { const s = SEGS.find(x => x.id === u); if (s) return s.t0; warn(`until "${u}": no such segment`); } return tOf(u, fb); };

    // runs: consecutive map segments share one map, one camera and pieces that may outlive their own segment
    const runs = [];
    SEGS.forEach((s, i) => {
      if (s.type !== "map") return;
      const prev = SEGS[i - 1];
      if (prev && prev.type === "map" && runs.length) runs[runs.length - 1].t1 = s.t1;
      else runs.push({ t0: s.t0, t1: s.t1 });
      s.__run = runs[runs.length - 1];
    });
    // a run that ends the film holds to its last frame (the tail after the last word), never cutting to black
    if (runs.length && SEGS[SEGS.length - 1].type === "map") runs[runs.length - 1].t1 = Math.max(runs[runs.length - 1].t1, TL.duration);
    const segEnd = s => s === SEGS[SEGS.length - 1] ? Math.max(s.t1, TL.duration) : s.t1;
    const runAt = t => runs.find(r => t >= r.t0 - 0.01 && t <= r.t1 + 0.01);

    // ── layers ──
    const root = el("g", { "data-map": 1 }, sceneG);
    el("rect", { x: -10, y: -10, width: W + 20, height: H + 20, fill: PAL.board }, root);
    const camG = el("g", {}, root);
    const mipG = el("g", {}, camG), paintG = el("g", { style: "mix-blend-mode: multiply" }, camG);
    const inkG = el("g", {}, root);
    // pieces live in one group per segment, straight under the scenes layer and marked data-seg, so check reads their text
    // as that segment's. A pinned piece's group carries the camera and the piece undoes its zoom: the text keeps a constant
    // size on screen, and in the group's own space it holds still while the camera pans (check measures movement there)
    const segG = {}, layers = [root];
    const pinG = id => segG["p" + id] || (segG["p" + id] = (g => (layers.push(g), g))(el("g", { "data-seg": id }, sceneG)));
    const fixG = id => segG["f" + id] || (segG["f" + id] = (g => (layers.push(g), g))(el("g", { "data-seg": id }, sceneG)));
    segs.forEach(s => { pinG(s.id); });
    segs.forEach(s => { fixG(s.id); });
    // the paper's texture is baked into the map (mapart.py); over the frame go a soft vignette and dust specks, a new
    // set every 8 frames, both drawn as vectors (a full-frame image overlay cost about 0.5 s a frame to render)
    const finishG = el("g", { "data-aux": 1 }, sceneG);
    layers.push(finishG);
    const LV = M ? M.levels : 1, TS = M ? M.tile : 1024, IMAGES = [];
    // each map size is a grid of tiles; a frame shows only the tiles of its size that the view touches
    const tileSet = (name, L, parent) => ((M && M.tiles[name]) || []).map(([c, r, w, h]) => {
      const k = 2 ** L, href = `maps/${name}/${r}_${c}.png`;
      IMAGES.push(href);
      return { el: el("image", { href, x: c * TS * k, y: r * TS * k, width: w * k, height: h * k, preserveAspectRatio: "none" }, parent),
               x0: c * TS * k, y0: r * TS * k, x1: (c * TS + w) * k, y1: (r * TS + h) * k };
    });
    const showTiles = (levels, L) => {
      const hw = W / (2 * cam.z) + 8, hh = H / (2 * cam.z) + 8;
      levels.forEach((ts, i) => ts.forEach(t => K.show(t.el, i === L && t.x1 > cam.mx - hw && t.x0 < cam.mx + hw && t.y1 > cam.my - hh && t.y0 < cam.my + hh)));
    };
    const mips = [...Array(LV)].map((_, L) => tileSet(`map-${L}`, L, mipG));
    const vg = el("radialGradient", { id: "mapvig", cx: "50%", cy: "50%", r: "72%" }, K.defs);
    el("stop", { offset: "62%", "stop-color": "#3a2e1c", "stop-opacity": 0 }, vg);
    el("stop", { offset: "100%", "stop-color": "#3a2e1c", "stop-opacity": 0.16 }, vg);
    el("rect", { x: 0, y: 0, width: W, height: H, fill: "url(#mapvig)" }, finishG);
    const dust = [0, 1, 2].map(k => {
      const g = el("g", { fill: PAL.ink }, finishG);
      for (let i = 0; i < 26; i++) el("circle", { cx: hash(k, i, 1) * W, cy: hash(k, i, 2) * H, r: 0.6 + hash(k, i, 3) * 1.8, "fill-opacity": 0.3 + hash(k, i, 4) * 0.4 }, g);
      return g;
    });

    // ── camera: keys of (t, lon, lat, zoom); each move eases (smoothstep) over 16-26 frames, zoom in log space ──
    const keys = [];
    segs.forEach(s => {
      const cs = (s.data || {}).camera;
      (Array.isArray(cs) ? cs : cs ? [cs] : []).forEach(c => {
        const [mx, my] = toMap(c.lon, c.lat);
        keys.push({ t: tOf(c, s.t0), mx, my, z: c.zoom || 0.6, frames: c.frames, seg: s.id, run: s.__run });
      });
    });
    keys.sort((a, b) => a.t - b.t);
    const clampView = (mx, my, z, k) => {
      const hw = W / (2 * z), hh = H / (2 * z);
      const cx = P.w <= 2 * hw ? P.w / 2 : Math.min(Math.max(mx, hw), P.w - hw), cy = P.h <= 2 * hh ? P.h / 2 : Math.min(Math.max(my, hh), P.h - hh);
      if (M && k && !k.warned && (Math.abs(cx - mx) * z > 4 || Math.abs(cy - my) * z > 4)) {
        k.warned = true; warn(`${k.seg}: the camera at zoom ${k.z} would show past the map's edge; it was held inside. Widen "bounds" (draw at least 25 degrees beyond any shot)`);
      }
      return [cx, cy];
    };
    keys.forEach(k => { [k.mx, k.my] = clampView(k.mx, k.my, k.z, k); });
    // each key starts from wherever the camera is when it begins (a move cut short by the next key hands on its state)
    keys.forEach((k, i) => {
      const p = keys[i - 1];
      if (!p || p.run !== k.run) { k.from = { mx: k.mx, my: k.my, z: k.z }; k.frames = 0; return; }
      k.from = camAt(k.t, i);
      if (k.frames == null) {
        const pan = Math.hypot(k.mx - k.from.mx, k.my - k.from.my) * Math.min(k.z, k.from.z) / W, zz = Math.abs(Math.log2(k.z / k.from.z));
        k.frames = Math.round(16 + 10 * Math.min(1, (pan + zz) / 2));
      }
    });
    function camAt(t, upto = keys.length) {
      let k = null;
      for (let i = 0; i < upto; i++) if (keys[i].t <= t + 1e-6) k = keys[i];
      if (!k) { const f = keys[0]; return f ? { mx: f.mx, my: f.my, z: f.z } : { mx: P.w / 2, my: P.h / 2, z: Math.max(W / P.w, H / P.h) }; }
      const p = k.frames ? sm(K.clamp01((t - k.t) / (k.frames / FPS))) : 1;
      const z = Math.exp(K.lerp(Math.log(k.from.z), Math.log(k.z), p));
      const [mx, my] = clampView(K.lerp(k.from.mx, k.mx, p), K.lerp(k.from.my, k.my, p), z);
      return { mx, my, z };
    }
    let cam = { mx: 0, my: 0, z: 1 };
    const scr = (lon, lat) => { const [x, y] = toMap(lon, lat); return [(x - cam.mx) * cam.z + W / 2, (y - cam.my) * cam.z + H / 2]; };

    // ── region paint: yellow, multiplied at 90%, revealed south to north over 16 frames on its word ──
    const paints = [];
    segs.forEach(s => ((s.data || {}).paint || []).forEach(pt => {
      const R = M && M.regions[pt.region];
      if (!R) { warn(`${s.id}: paint region "${pt.region}" is not in the map's "featured"`); return; }
      const id = "mpaint" + paints.length, clip = K.clipRect(id);
      const g = el("g", { "clip-path": `url(#${id})`, opacity: 0.9 }, paintG);
      const imgs = [...Array(LV)].map((_, L) => tileSet(`paint-${pt.region}-${L}`, L, g));
      paints.push({ g, clip, imgs, box: R.box, t0: tOf(pt, s.t0 + 0.2), t1: untilT(pt.until, s.__run.t1), frames: pt.frames || 16 });
    }));

    // ── marker ink: a variable-width outline drawn in screen space every frame, so its weight holds at any zoom ──
    const noise1 = (u, seed) => Math.sin(u * 0.11 + seed * 9.1) * 0.6 + Math.sin(u * 0.27 + seed * 3.7) * 0.3 + Math.sin(u * 0.53 + seed * 1.3) * 0.1;
    function resample(pts, step = 1.6) {
      const out = [pts[0]], cum = [0];
      for (let i = 1; i < pts.length; i++) {
        const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], d = Math.hypot(x1 - x0, y1 - y0);
        let acc = cum[cum.length - 1];
        const n = Math.max(1, Math.ceil(d / step));
        for (let k = 1; k <= n; k++) { out.push([x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n]); cum.push(acc + d * k / n); }
      }
      return { pts: out, cum };
    }
    // one stroke: pts (screen), width w, the first `upto` px of it, tapered ends; returns path data
    function strokeD(pts, w, upto, seed, taper = 9) {
      const n = pts.length;
      if (n < 2) return "";
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
      const all = left.concat(right.reverse());
      return "M" + all.map(p => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L") + "Z";
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
      q.forEach((p, i) => { const inOn = (cum[i] % (on + off)) < on; if (inOn) cur.push(p); else if (cur.length) { out.push(cur); cur = []; } });
      if (cur.length) out.push(cur);
      return out;
    };
    const boilSeed = (t, id) => Math.floor(frameOf(t) / 8) % 3 + id * 7;
    const jitter = (pts, seed, a = 0.8) => pts.map((p, i) => [p[0] + (hash(seed, i, 1) - 0.5) * 2 * a, p[1] + (hash(seed, i, 2) - 0.5) * 2 * a]);

    const inks = [];
    segs.forEach(s => ((s.data || {}).ink || []).forEach((d, k) => {
      const kind = d.kind || "arrow";
      const path = el("path", { fill: PAL.ink }, inkG);
      const cray = el("path", { fill: "none", stroke: PAL.ink, "stroke-width": 2, "stroke-linecap": "round" }, inkG);
      const hl = kind === "highlight" ? el("rect", { fill: PAL.yellow, style: "mix-blend-mode: multiply" }, inkG) : null;
      const t0 = tOf(d, s.t0 + 0.3);
      inks.push({ d, kind, path, cray, hl, id: inks.length + 1, t0, t1: untilT(d.until, d.keep ? s.__run.t1 : segEnd(s)),
                  frames: d.frames || (kind === "x" ? 6 : kind === "circle" ? 14 : 12), name: d.id || `${s.id}.ink${k}` });
    }));
    function inkGeom(q) {      // control points on screen, for this frame
      const d = q.d;
      if (q.kind === "circle" || q.kind === "x") {
        const [x, y] = scr(...ll(d.at_place || d.place || d));
        return { x: x + (d.dx || 0), y: y + (d.dy || 0) };
      }
      const a = scr(...ll(d.from)), b = scr(...ll(d.to));
      return { pts: quad(a, b, d.bend != null ? d.bend : q.kind === "arc" ? 0.22 : 0.14) };
    }
    function drawInk(q, t) {
      const on = t >= q.t0 && t < q.t1;
      K.show(q.path, on); K.show(q.cray, on); if (q.hl) K.show(q.hl, on);
      if (!on) return;
      const tq = twos(t), p = K.clamp01((tq - q.t0) / (q.frames / FPS)), seed = boilSeed(t, q.id), w = q.d.width || 5.5;
      const G = inkGeom(q);
      let d = "", c = "";
      if (q.kind === "circle") {
        const r = q.d.r || 70, n = 60, pts = [];
        for (let i = 0; i <= n; i++) { const u = i / n, a = -1.75 + u * 2 * Math.PI * 1.08, rr = r * (1 + 0.06 * u); pts.push([G.x + Math.cos(a) * rr, G.y + Math.sin(a) * rr * 0.86]); }
        const jp = jitter(pts, seed), L = r * 2 * Math.PI * 1.1;
        d = strokeD(jp, w, L * p, seed); c = crayonD(jp, w, L * p, seed);
      } else if (q.kind === "x") {
        const s = q.d.size || 20, a = [[G.x - s, G.y - s], [G.x + s, G.y + s]], b = [[G.x + s, G.y - s], [G.x - s, G.y + s]];
        d = strokeD(jitter(a, seed), w, 2.9 * s * K.clamp01(p * 2), seed) + strokeD(jitter(b, seed + 3), w, 2.9 * s * K.clamp01(p * 2 - 1), seed + 3);
      } else if (q.kind === "highlight") {
        const pts = G.pts, x0 = Math.min(pts[0][0], pts[pts.length - 1][0]), y = pts[0][1], len = Math.abs(pts[pts.length - 1][0] - pts[0][0]);
        K.set(q.hl, { x: x0, y: y - (q.d.width || 30) / 2, width: len * p, height: q.d.width || 30 }); return;
      } else {
        const pts = jitter(q.G0 = G.pts, seed), { cum } = resample(pts), L = cum[cum.length - 1], upto = L * p;
        if (q.kind === "arc" || q.kind === "trail" || q.d.dashed) {
          let acc = 0;
          dashes(pts, 27, 19).forEach((dp, i) => {
            const { cum: dc } = resample(dp, 2), dl = dc[dc.length - 1], start = acc;
            acc = start + dl + 19;
            if (start < upto) d += strokeD(dp, w * 0.9, upto - start, seed + i, 4);
          });
        } else { d = strokeD(pts, w, upto, seed); c = crayonD(pts, w, upto, seed); }
        if (q.kind === "arrow" && p > 0.85) {      // the head: two strokes back from the tip at +-0.48 rad, 26 px
          const e = pts[pts.length - 1], f = pts[pts.length - 6] || pts[0], ang = Math.atan2(e[1] - f[1], e[0] - f[0]), hp = K.clamp01((p - 0.85) / 0.15);
          [-0.48, 0.48].forEach((da, i) => {
            const b = [e[0] - 26 * Math.cos(ang + da), e[1] - 26 * Math.sin(ang + da)];
            d += strokeD(jitter([e, b], seed + 5 + i), w, 26 * hp, seed + 5 + i, 4);
          });
        }
      }
      K.set(q.path, { d }); K.set(q.cray, { d: c || "M0 0" });
    }

    // ── collage pieces, pinned to places at a constant screen size ──
    const pieces = [], cards = [];
    const caslon = { "font-family": `"${FONTS.caslon}", Georgia, serif` };
    function addTextLines(g, lines, attrs, lh) { return lines.map((ln, i) => K.text("", Object.assign({ x: 0, y: i * lh }, attrs), g)); }
    segs.forEach(s => ((s.data || {}).pieces || []).forEach((d, k) => {
      // g: place, zoom and slap; boil: the jitter, marked data-boil so check doesn't read it as the text moving; inner: the piece
      const kind = d.kind || "cutout", g = el("g", {}, kind === "title" || kind === "sheet" ? fixG(s.id) : pinG(s.id)), boil = el("g", { "data-boil": 1 }, g), inner = el("g", {}, boil);
      const t0 = tOf(d, s.t0 + 0.2), words = String(d.text || (d.lines || []).join(" ") || "").split(/\s+/).filter(Boolean).length;
      // text that must be read stays up long enough to read (max(2 s, 1 s + words / 3), the engine's rule, plus its write-on),
      // past its own segment's end if it has to; "until" or "keep" overrides
      const need = words ? Math.max(2, 1 + words / 3) + 0.5 : 0;
      const q = { d, kind, g, boil, inner, id: 100 + pieces.length, t0, t1: untilT(d.until, d.keep ? s.__run.t1 : Math.min(s.__run.t1, Math.max(segEnd(s), t0 + need))), seg: s, name: d.id || `${s.id}.${kind}${k}` };
      const rot = d.rot != null ? d.rot : (hash(q.id, 9) - 0.5) * 4;
      q.rot = rot;
      if (kind === "cutout" || kind === "document") {
        const w = d.w || 260, h = d.h || w;
        q.img = el("image", { href: d.image, x: -w / 2, y: -h / 2, width: w, height: h, preserveAspectRatio: "xMidYMid meet" }, inner);
        IMAGES.push(d.image);
        if (d.swap) { IMAGES.push(d.swap.image); q.swapT = tOf(d.swap, q.t1); }
      } else if (kind === "tag") {
        const size = Math.max(READ, d.size || READ);
        q.rect = el("rect", { fill: PAL.yellow }, inner);
        q.txt = K.text(d.text.toUpperCase(), { "font-family": `"${FONTS.elite}", "Courier New", monospace`, "font-size": size, fill: PAL.ink, "letter-spacing": size * 0.08, x: 0, y: 0, "text-anchor": "middle", "dominant-baseline": "central" }, inner);
        q.size = size;
        K.label(d.text, q.t0, q.t1, "map tag");
      } else if (kind === "note") {
        const size = Math.max(READ, d.size || 64), lines = String(d.text).toLowerCase().split("\n");
        q.lines = lines; q.lh = size * 1.1;
        q.tx = addTextLines(inner, lines, { "font-family": `"${FONTS.marker}", "Comic Sans MS", cursive`, "font-size": size, fill: PAL.ink, "text-anchor": d.anchor || "middle" }, q.lh);
        K.label(d.text, q.t0, q.t1, "map note");
      } else if (kind === "ransom") {
        q.letters = ransom(inner, d.text, d.size || 110, q.id);
        K.label(d.text, q.t0, q.t1, "map ransom letters");
      } else if (kind === "splash") {
        const r = d.r || 40;
        [...Array(d.n || 5)].forEach((_, i) => {
          const a = hash(q.id, i, 3) * 6.28, dd = r * (0.4 + hash(q.id, i, 4)), rr = 3 + hash(q.id, i, 5) * 9;
          el("path", { d: blob(Math.cos(a) * dd, Math.sin(a) * dd, rr, q.id + i), fill: PAL.ink }, inner);
        });
      } else if (kind === "title") {
        const lines = d.lines || [d.text], size = d.size || 68, lh = size * 1.18, cw = d.w || 560, ch = 70 + lines.length * lh;
        el("rect", { x: 0, y: 0, width: cw, height: ch, fill: PAL.beige }, inner);
        el("rect", { x: 0, y: 0, width: cw, height: ch, fill: "none", stroke: PAL.ink, "stroke-opacity": 0.12, "stroke-width": 2 }, inner);
        if (d.fig) {
          el("rect", { x: 28, y: -24, width: 170, height: 48, fill: PAL.yellow }, inner);
          K.text(d.fig, { "font-family": `"${FONTS.elite}", monospace`, "font-size": 26, fill: PAL.ink, x: 113, y: 0, "text-anchor": "middle", "dominant-baseline": "central", "letter-spacing": 3, "data-aux": 1 }, inner);
        }
        q.lines = lines; q.tx = lines.map((_, i) => K.text("", Object.assign({}, caslon, { "font-size": size, fill: PAL.ink, x: 34, y: 50 + size + i * lh }), inner));
        q.fixed = VERT ? [SAFE.x0 + 10, SAFE.y0 + 30] : [56, 64]; q.rot = d.rot != null ? d.rot : -1.2;
        K.label(lines.join(" "), q.t0, q.t1, "map title card");
      } else if (kind === "sheet") {
        const size = d.size || 62, cw = d.w || V2(1100, 900), ws = d.word_size || 110;
        const h = 40 + size * 1.2 + (d.word ? 30 + ws * 1.08 : 0) + 40;      // sized from what it holds
        el("rect", { x: -cw / 2, y: -h, width: cw, height: h, fill: PAL.beige }, inner);
        q.txt = K.text(d.text, Object.assign({}, caslon, { "font-size": size, "font-weight": 700, fill: PAL.ink, x: 0, y: -h + 40 + size, "text-anchor": "middle" }), inner);
        if (d.word) {
          q.wordY = -h + 40 + size * 1.2 + 30 + ws * 0.54;
          q.hl = el("rect", { fill: PAL.yellow, style: "mix-blend-mode: multiply" }, inner);
          const wg = el("g", { transform: `translate(0 ${q.wordY.toFixed(1)})` }, inner);
          q.letters = ransom(wg, d.word, d.word_size || 110, q.id);
          q.wordW = q.letters.width;
        }
        q.fixed = [W / 2, VERT ? SAFE.y1 - 20 : H - 40]; q.rot = d.rot != null ? d.rot : 0.6;
        K.label(d.text + (d.word ? " " + d.word : ""), q.t0, q.t1, "map sheet");
      } else warn(`${s.id}: unknown piece kind "${kind}"`);
      if (d.splash && kind !== "splash") {
        [...Array(5)].forEach((_, i) => {
          const a = hash(q.id, i, 3) * 6.28, dd = (d.w || 200) * (0.45 + hash(q.id, i, 4) * 0.25), rr = 3 + hash(q.id, i, 5) * 8;
          boil.insertBefore(el("path", { d: blob(Math.cos(a) * dd, Math.sin(a) * dd, rr, q.id + i), fill: PAL.ink }, boil), inner);
        });
      }
      (q.fixed ? cards : pieces).push(q);
    }));
    function V2(a, b) { return VERT ? b : a; }
    function blob(x, y, r, seed) {
      const n = 10;
      return "M" + [...Array(n)].map((_, i) => { const a = i / n * 6.283, rr = r * (0.7 + 0.5 * hash(seed, i)); return (x + Math.cos(a) * rr).toFixed(1) + " " + (y + Math.sin(a) * rr).toFixed(1); }).join("L") + "Z";
    }
    // cut-paper letters: each its own square of ink, beige or yellow, in Caslon Bold, Anton or Special Elite
    function ransom(g, text, size, seed) {
      const faces = [[`"${FONTS.caslon}", Georgia, serif`, 700], [`"${FONTS.anton}", Impact, sans-serif`, 400], [`"${FONTS.elite}", monospace`, 400]];
      const grounds = [[PAL.ink, PAL.beige], [PAL.beige, PAL.ink], [PAL.yellow, PAL.ink]];
      const chars = [...String(text).toUpperCase()], out = [];
      let x = 0;
      chars.forEach((ch, i) => {
        if (ch === " ") { x += size * 0.45; return; }
        const f = faces[(i + seed) % 3], gr = grounds[(i * 2 + seed) % 3], w = size * (ch === "I" || ch === "1" ? 0.62 : 0.86), h = size * 1.08;
        const lg = el("g", {}, g);
        el("rect", { x: -w / 2, y: -h / 2, width: w, height: h, fill: gr[0] }, lg);
        K.text(ch, { "font-family": f[0], "font-weight": f[1], "font-size": size * 0.82, fill: gr[1], x: 0, y: 0, "text-anchor": "middle", "dominant-baseline": "central" }, lg);
        out.push({ g: lg, x: x + w / 2, dy: (hash(seed, i, 7) - 0.5) * size * 0.14, rot: (hash(seed, i, 8) - 0.5) * 12 });
        x += w + size * 0.06;
      });
      out.forEach(l => { l.x -= x / 2; });
      out.width = x;
      return out;
    }
    const slap = f => f < 0 ? 0 : f < 2 ? 1.1 : f < 4 ? 0.98 : 1;     // arrival: 1.1, 0.98, 1.0 on twos, never a fade

    // movers: a piece with "along": "<ink id>" travels that line from its own cue over "travel" seconds
    function along(q, t) {
      const ink = inks.find(i => i.name === q.d.along);
      if (!ink) return null;
      const G = inkGeom(ink).pts, T = q.d.travel || 1.2, p = sm(K.clamp01((twos(t) - q.t0) / T));
      const i = Math.min(G.length - 2, Math.floor(p * (G.length - 1))), u = p * (G.length - 1) - i;
      const a = G[i], b = G[i + 1];
      return { x: K.lerp(a[0], b[0], u), y: K.lerp(a[1], b[1], u), rot: 4 * Math.sin(p * 9) * (1 - p) };
    }

    let fontsOk = false, measured = false;
    function measure() {
      pieces.concat(cards).forEach(q => {
        if (q.kind === "tag") {
          const fullTxt = q.txt.textContent, bw = q.txt.getComputedTextLength() + q.size * 0.9, bh = q.size * 1.5;
          K.set(q.rect, { x: -bw / 2, y: -bh / 2, width: bw, height: bh }); void fullTxt;
        }
      });
      measured = true;
    }
    function typed(q, t, perFrame) {        // letters on at perFrame a frame, line after line
      let n = Math.max(0, Math.floor((frameOf(t) - frameOf(q.t0) - 4) * perFrame));
      q.lines.forEach((ln, i) => { q.tx[i].textContent = ln.slice(0, Math.max(0, n)); n -= ln.length; });
    }
    function drawPiece(q, t) {
      const on = t >= q.t0 && t < q.t1;
      K.show(q.g, on);
      if (!on) return;
      const f = frameOf(t) - frameOf(q.t0), k = Math.floor(frameOf(t) / 8) % 3;
      const bx = (hash(q.id, k, 1) - 0.5) * 3, by = (hash(q.id, k, 2) - 0.5) * 3, br = (hash(q.id, k, 3) - 0.5) * 0.8;   // boil
      let x, y, rot = q.rot + br;
      if (q.fixed) [x, y] = q.fixed;
      else if (q.d.along) { const a = along(q, t); if (a) { x = a.x; y = a.y; rot += a.rot; } }
      if (x == null) { const [sx, sy] = scr(...ll(q.d)); x = sx + (q.d.dx || 0); y = sy + (q.d.dy || 0); }
      const s = q.kind === "note" || q.kind === "ransom" ? 1 : slap(Math.floor(f / 2) * 2);
      // a pinned piece sits in its camera group: screen position back to map space, then its zoom undone
      const pre = q.fixed ? "" : `translate(${((x - W / 2) / cam.z + cam.mx).toFixed(3)} ${((y - H / 2) / cam.z + cam.my).toFixed(3)}) scale(${(1 / cam.z).toFixed(5)}) translate(${(-x).toFixed(3)} ${(-y).toFixed(3)}) `;
      q.g.setAttribute("transform", pre + `translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${(rot - br).toFixed(2)}) scale(${s})`);
      q.boil.setAttribute("transform", `translate(${bx.toFixed(2)} ${by.toFixed(2)}) rotate(${br.toFixed(2)})`);
      if (q.img && q.swapT != null) q.img.setAttribute("href", t >= q.swapT ? q.d.swap.image : q.d.image);
      if (q.kind === "note") typed(q, t, 1.3);
      if (q.kind === "title") typed(q, t, 1);
      if (q.letters) {
        const start = q.kind === "sheet" ? 10 : 0;
        q.letters.forEach((l, i) => {
          const at = start + Math.floor(i * 3.5), lf = f - at;
          K.show(l.g, lf >= 0);
          if (lf >= 0) l.g.setAttribute("transform", `translate(${l.x.toFixed(1)} ${l.dy.toFixed(1)}) rotate(${l.rot.toFixed(1)}) scale(${slap(Math.floor(lf / 2) * 2)})`);
        });
        if (q.hl) {
          const done = start + Math.floor(q.letters.length * 3.5) + 2, hp = K.clamp01((f - done) / 10);
          const hw = q.wordW + 40;
          K.set(q.hl, { x: -hw / 2, y: q.wordY + (q.d.word_size || 110) * 0.38, width: hw * hp, height: 30 });
        }
      }
    }

    // ── per frame ──
    let anyRun = false;
    function frame(t) {
      const r = runAt(t);
      anyRun = !!r;
      layers.forEach(g => K.show(g, anyRun));
      if (!anyRun) return;
      if (fontsOk && !measured) measure();
      const tq = twos(t);
      cam = camAt(tq);
      const camT = `translate(${W / 2} ${H / 2}) scale(${cam.z.toFixed(5)}) translate(${(-cam.mx).toFixed(2)} ${(-cam.my).toFixed(2)})`;
      camG.setAttribute("transform", camT);
      Object.keys(segG).forEach(k => { if (k[0] === "p") segG[k].setAttribute("transform", camT); });
      const L = Math.max(0, Math.min(LV - 1, Math.floor(Math.log2(1 / cam.z) + 0.15)));      // the map size nearest the zoom
      showTiles(mips, L);
      paints.forEach(p => {
        const on = tq >= p.t0 && t < p.t1;
        K.show(p.g, on);
        if (!on) return;
        showTiles(p.imgs, L);
        const u = K.clamp01((tq - p.t0) / (p.frames / FPS)), [bx, by, bw, bh] = p.box;
        K.set(p.clip, { x: bx - 4, width: bw + 8, y: by + bh * (1 - u) - 4, height: bh * u + 8 });
      });
      inks.forEach(q => drawInk(q, t));
      pieces.concat(cards).forEach(q => drawPiece(q, t));
      const dk = Math.floor(frameOf(t) / 8) % 3;
      dust.forEach((d, i) => K.show(d, i === dk));
    }
    const ready = Promise.all(Object.keys(FILES).map(k => document.fonts.load(`40px "${FONTS[k]}"`).catch(() => warn(`font ${FILES[k]} missing: run mapart.py fonts`))))
      .then(() => document.fonts.ready).then(() => { fontsOk = true; measured = false; });

    return {
      images: IMAGES, ready, frame,
      // the segment's own group stays empty: the map and its pieces live in the shared layer, so they carry across segments
      build: () => () => {},
      // for check: piece arrivals (they slap in on purpose) and the camera moves
      events: () => ({ pieces: pieces.concat(cards).map(q => q.t0), ink: inks.map(q => q.t0), moves: keys.filter(k => k.frames).map(k => [k.t, k.frames / FPS]) }),
    };
  };
})();

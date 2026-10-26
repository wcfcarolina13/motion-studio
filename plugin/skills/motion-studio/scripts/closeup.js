// motion-studio close-ups: a head-and-hands character for close shots, drawn in SVG and posed as a pure function of t.
// Load after kit.js and figure.js. No body: a head and two stubby hands peek over an edge (a phone, a desk, the bottom of
// the frame) and act through big, simple face parts. The API mirrors F.figure, F.place, F.at and F.idle:
//
//   const me = F.closeup.figure(parent, { skin: "#ff7a78", hair: "tuft", glasses: ["halfmoon", "arch", "slit"], front: frontG });
//   K.run(t => F.closeup.place(me, 960, 820, 420, F.closeup.idle(t, 3, F.closeup.at(t, [[0, "hidden"], [1.2, "peek"], [2, "up", { hold: 14 }]]))));
//
// (x, y) in place is the middle of the edge the character peeks over; h is the head's height in px. Everything behind
// the edge is clipped to it, so "hidden" really shows nothing. Fingertips that grip the edge are drawn in `front` (a group
// the caller puts in front of whatever forms the edge); without it they go on top of the figure.
// Poses are flat objects of numbers, so they blend (blend) and spring (at is F.keyer, as F.at: anticipation before big
// moves, overshoot then settle on the head and hands, never on face settings, and `hold` frames at the overshoot peak).
(function () {
  const F = window.F;
  const { clamp01, lerp, prog, smooth } = K;
  const NS = "http://www.w3.org/2000/svg";
  const el = (tag, a, p) => { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  const f4 = v => (Math.round(v * 1e4) / 1e4).toString();

  const CU = {};
  F.closeup = CU;

  // ── poses ────────────────────────────────────────────────
  // rise: 0 hidden below the edge .. 1 up (a little over 1 pops higher); tilt: head roll, degrees (+ clockwise);
  // slide: head x, in head heights; squash: + flatter and wider, - taller; hl*/hr*: the screen-left and screen-right hands:
  // Rise 0..1, X (head heights, + outward), Grip 0..1 (fingers over the edge). Face: brow -1 angry .. 1 worried;
  // mouth -1 frown .. 1 smile; open 0..1; look/lookY -1..1 (screen left/up is -1); blink 0..1; pupil size (1 normal);
  // lens: 0 .. n-1 across the figure's lens shapes.
  const base = { rise: 1, tilt: 0, slide: 0, squash: 0, hlRise: 1, hlX: 0, hlGrip: 1, hrRise: 1, hrX: 0, hrGrip: 1,
                 brow: 0, mouth: 0.2, open: 0, look: 0, lookY: 0, blink: 0, pupil: 1, lens: 0 };
  CU.POSES = {
    hidden:     { rise: 0, hlRise: 0, hrRise: 0, hlGrip: 0, hrGrip: 0, hlX: 0.15, hrX: 0.15, mouth: 0 },
    peek:       { rise: 0.75, brow: 0.35, mouth: 0, pupil: 1.25, lookY: -0.1 },
    up:         {},
    worried:    { rise: 0.95, tilt: -5, squash: 0.06, brow: 1, mouth: -0.7, open: 0.12, pupil: 0.85, hlX: -0.08, hrX: -0.08 },
    shocked:    { rise: 1.1, squash: -0.16, brow: 1, mouth: -0.2, open: 0.85, pupil: 0.5, lens: 1, hlRise: 1.06, hrRise: 1.06 },
    realise:    { rise: 1.04, tilt: -7, squash: -0.06, brow: 0.8, mouth: -0.1, open: 0.45, pupil: 1.35, lens: 1, lookY: -0.2 },
    suspicious: { tilt: 13, slide: 0.24, squash: 0.08, brow: -0.75, mouth: -0.35, pupil: 0.8, lens: 2, look: 0.7, hlX: 0.06, hrX: 0.3 },
    relief:     { rise: 0.95, tilt: 5, squash: 0.12, brow: 0.55, mouth: 0.85, blink: 0.82, pupil: 1, hlX: 0.06, hrX: 0.06 },
  };
  for (const k in CU.POSES) CU.POSES[k] = Object.assign({}, base, CU.POSES[k]);
  const resolve = p => typeof p === "string" ? (CU.POSES[p] || CU.POSES.up) : Object.assign({}, base, p);
  // a named pose with some values changed: CU.pose("hidden", { hlRise: 1 })
  CU.pose = (name, over = {}) => Object.assign({}, resolve(name), over);
  CU.blend = (a, b, p) => { a = resolve(a); b = resolve(b); const o = {}; for (const k in base) o[k] = lerp(a[k], b[k], p); return o; };

  // keys: [[t, pose], ...] or [[t, pose, {hold, z, w}], ...], as F.at, except that anticipate is on unless o says
  // otherwise. A big change first moves 12% the other way over 0.12 s (ending on the key), then the head and hands spring
  // to the target and overshoot by about 5%; face settings and grips never overshoot, and nothing dips below the edge in
  // the counter-move. `hold` (frames) freezes the pose at the top of its overshoot: the reaction poses hold 14 frames
  // (about 480 ms) unless the key says otherwise. z and w (per key or per call) tune the spring as in F.at.
  // Call as at(t, keys, { anticipate }) or at(t, keys, dur, { anticipate }).
  const BODY = ["rise", "tilt", "slide", "squash", "hlRise", "hlX", "hrRise", "hrX"];
  CU.HOLDS = { worried: 14, shocked: 14, realise: 14, suspicious: 14, relief: 14 };
  // weight: how much each body value counts toward a "big" change (over 30), in degree-like units (F.at: 80 degrees of
  // arms, head and lean)
  const at = F.keyer({ resolve, blend: CU.blend, body: BODY, big: [BODY, 30], floor: { rise: 0, hlRise: 0, hrRise: 0 },
                       weight: { rise: 90, tilt: 1, slide: 60, squash: 60, hlRise: 45, hlX: 30, hrRise: 45, hrX: 30 }, holds: CU.HOLDS });
  CU.at = (t, keys, dur = 0.45, o) => {
    if (typeof dur === "object") { o = dur; dur = 0.45; }
    return at(t, keys, dur, Object.assign({ anticipate: true }, o || {}));
  };

  // idle on top of any pose: breath (a small rise and tilt), a blink every few seconds, the hands settling.
  // Deterministic per seed, the same blink curve as F.idle.
  CU.idle = (t, seed = 1, pose = "up") => {
    const p = Object.assign({}, resolve(pose));
    const breath = Math.sin(t * 2 * Math.PI / 3.6 + seed);
    const up = smooth(clamp01(p.rise / 0.5));          // no breathing while hidden
    p.rise += breath * 0.012 * up; p.tilt += Math.sin(t * 0.9 + seed * 2) * 1.2 * up;
    p.hlRise += Math.sin(t * 2 * Math.PI / 3.6 + seed + 0.8) * 0.01 * smooth(clamp01(p.hlRise));
    p.hrRise += Math.sin(t * 2 * Math.PI / 3.6 + seed + 1.9) * 0.01 * smooth(clamp01(p.hrRise));
    const period = 3.1 + (seed % 5) * 0.37, ph = (t + seed * 1.7) % period;
    p.blink = Math.max(p.blink, ph < 0.13 ? Math.sin(ph / 0.13 * Math.PI) : 0);
    return p;
  };

  // ── shapes (head units: the head is 2 high, centred on 0,0) ──
  // a closed Catmull-Rom curve through points, as cubic Beziers: every path made this way from N points has the same
  // command structure, which is what lets two lens shapes morph by a plain lerp
  const curve = pts => {
    const n = pts.length, P = i => pts[(i + n) % n];
    let d = `M ${f4(P(0)[0])} ${f4(P(0)[1])}`;
    for (let i = 0; i < n; i++) {
      const p0 = P(i - 1), p1 = P(i), p2 = P(i + 1), p3 = P(i + 2);
      d += ` C ${f4(p1[0] + (p2[0] - p0[0]) / 6)} ${f4(p1[1] + (p2[1] - p0[1]) / 6)} ${f4(p2[0] - (p3[0] - p1[0]) / 6)} ${f4(p2[1] - (p3[1] - p1[1]) / 6)} ${f4(p2[0])} ${f4(p2[1])}`;
    }
    return d + " Z";
  };
  // sample a star-shaped region from (0, 0) along N rays (the same rays for every shape, so points correspond)
  const LN = 24;
  const rays = inside => [...Array(LN)].map((_, i) => {
    const a = -Math.PI / 2 + i * 2 * Math.PI / LN, dx = Math.cos(a), dy = Math.sin(a);
    let lo = 0, hi = 1;
    for (let k = 0; k < 30; k++) { const m = (lo + hi) / 2; if (inside(dx * m, dy * m)) lo = m; else hi = m; }
    return [dx * lo, dy * lo];
  });
  const sup = (x, y, rx, ry, e) => Math.pow(Math.abs(x / rx), e) + Math.pow(Math.abs(y / ry), e) < 1;
  // lens shapes, one eye (the screen-left one; the other is its mirror), about 0.66 wide.
  //   halfmoon: flat-topped reading lenses, round underneath (calm, a bit sleepy)
  //   arch: tall lenses, round on top and flat along the bottom: the half-moon turned over (alarm, eyes wide)
  //   slit: a narrow rounded bar (suspicion)
  //   oval: a plain eye, used when the figure has no glasses
  // CU.lens(inside) makes a lens from any star-shaped region around the eye centre, (x, y) => true inside, in head units;
  // a lens made this way morphs smoothly into every other one
  CU.lens = inside => curve(rays(inside));
  CU.LENSES = {
    halfmoon: CU.lens((x, y) => y > -0.1 && sup(x, y - 0.02, 0.34, 0.3, 2.2)),
    arch: CU.lens((x, y) => Math.abs(x) < 0.27 && y < 0.24 && (y > -0.16 || x * x + (y + 0.16) * (y + 0.16) < 0.27 * 0.27)),
    slit: CU.lens((x, y) => sup(x, y - 0.03, 0.36, 0.11, 3.2)),
    oval: CU.lens((x, y) => sup(x, y, 0.24, 0.29, 2)),
  };
  const HEAD = curve([[0, -1], [0.55, -0.98], [0.92, -0.82], [1.06, -0.35], [1.14, 0.3], [1.02, 0.78], [0.58, 0.99], [0, 1.03],
                      [-0.58, 0.99], [-1.02, 0.78], [-1.14, 0.3], [-1.06, -0.35], [-0.92, -0.82], [-0.55, -0.98]]);
  const HAIRS = {
    none: null,
    // a short fringe band along the top of the head, clipped to it
    fringe: "M -1.4 -1.4 L 1.4 -1.4 L 1.4 -0.8 Q 1.05 -0.9 0.7 -0.84 Q 0.4 -0.94 0.08 -0.86 Q -0.3 -0.95 -0.62 -0.85 Q -0.95 -0.93 -1.4 -0.8 Z",
  };
  HAIRS.tuft = HAIRS.fringe;   // tuft = the fringe plus a sprout that leans with the head
  const SPROUT = "M -0.02 -0.9 C 0.0 -1.32 0.3 -1.58 0.66 -1.52 C 0.5 -1.38 0.36 -1.12 0.26 -0.92 Z";
  const BROW = "M -0.2 -0.055 L 0.2 -0.055 A 0.055 0.055 0 0 1 0.2 0.055 L -0.2 0.055 A 0.055 0.055 0 0 1 -0.2 -0.055 Z";
  const EYE_X = 0.42, EYE_Y = -0.1, FRAME = 1.24;
  const NUM = /-?\d*\.?\d+(?:e-?\d+)?/g;
  const nums = d => d.match(NUM).map(Number);
  const minY = d => { const v = nums(d); let m = 1e9; for (let i = 1; i < v.length; i += 2) m = Math.min(m, v[i]); return m; };

  // ── build ────────────────────────────────────────────────
  // opts: skin (fill: colour or url(#gradient)), skinShade (ears, nose; colour), hair ("tuft" | "fringe" | "none"),
  //       hairColor, hand (fill; default the skin), handShade (fingertips), glasses ("none" or a list of lens names from
  //       CU.LENSES or raw path strings, morphed in order by `lens`), frame (glasses colour), white, pupilColor, mouthColor,
  //       tongue, cheek, facing ("front" | "left" | "right": the face turns a little that way), front (a parent for the
  //       fingertips that hang over the edge, drawn in front of it).
  let nfig = 0;
  CU.figure = (parent, opts = {}) => {
    const o = Object.assign({ skin: "#e8a07a", skinShade: "#c97b5a", hair: "tuft", hairColor: "#2a1a24", hand: null, handShade: null,
                              glasses: "none", frame: "#2a1a24", white: "#fffaf4", pupilColor: "#2a1a24", mouthColor: "#4a1630",
                              tongue: "#e86a6a", cheek: "#e5484f", facing: "front", front: null }, opts);
    o.hand = o.hand || o.skin; o.handShade = o.handShade || o.skinShade;
    const id = "cu" + (nfig++), svg = parent.ownerSVGElement || parent;
    let defs = svg.querySelector("defs"); if (!defs) defs = el("defs", {}, svg);
    const glasses = Array.isArray(o.glasses) && o.glasses.length;
    const shapes = (glasses ? o.glasses : ["oval"]).map(s => {
      if (CU.LENSES[s]) return CU.LENSES[s];
      if (!/^\s*[Mm][\s\d.,-]/.test(s)) throw new Error(`closeup: unknown lens "${s}" (${Object.keys(CU.LENSES).join(", ")}, or a path)`);
      return s;
    });
    // a lens path the morph can't blend into its neighbour (different commands or point count) snaps at the halfway mark
    const shape = d => d.replace(NUM, "#");
    shapes.slice(1).forEach((d, i) => {
      if (shape(d) !== shape(shapes[i]))
        (window.STUDIO = window.STUDIO || {}, window.STUDIO.warnings = window.STUDIO.warnings || []).push(
          `closeup: lenses ${i + 1} and ${i + 2} have different point structures, so the morph between them snaps halfway; make both with F.closeup.lens`);
    });
    // the edge: everything behind it is clipped to the half-plane above y (updated by place)
    const clip = el("clipPath", { id: id + "edge", clipPathUnits: "userSpaceOnUse" }, defs);
    const edge = el("rect", { x: -1e5, y: -1e5, width: 2e5, height: 1e5 }, clip);
    const root = el("g", Object.assign({ "clip-path": `url(#${id}edge)` }, o.id ? { "data-who": o.id } : {}), parent);
    const body = el("g", {}, root);
    const front = el("g", {}, o.front || parent);
    const g = {};
    // hands, behind the head
    const hand = side => {
      const hg = el("g", {}, body), fg = el("g", {}, front);
      // a stubby mitten: a soft block with three knuckle bumps along the top
      el("path", { d: curve([[-0.3, -0.36], [-0.18, -0.46], [-0.07, -0.4], [0.04, -0.47], [0.15, -0.41], [0.26, -0.44], [0.33, -0.3],
                             [0.35, 0.2], [0.3, 0.9], [-0.3, 0.9], [-0.36, 0.2]]), fill: o.hand }, hg);
      // the thumb, toward the middle of the frame
      el("ellipse", { cx: side * -0.3, cy: -0.05, rx: 0.12, ry: 0.2, fill: o.hand, transform: `rotate(${side * -20} ${side * -0.3} -0.05)` }, hg);
      // fingertips over the edge, in front of it: they grow down from the edge line
      const tips = el("g", {}, fg);
      [-0.19, 0.0, 0.19].forEach(fx => el("rect", { x: fx - 0.075, y: -0.06, width: 0.15, height: 0.3, rx: 0.075, fill: o.hand }, tips));
      [-0.19, 0.0, 0.19].forEach(fx => el("rect", { x: fx - 0.075, y: 0.16, width: 0.15, height: 0.08, rx: 0.04, fill: o.handShade, opacity: 0.55 }, tips));
      return { hg, fg, tips };
    };
    g.hl = hand(-1); g.hr = hand(1);
    // the head
    g.head = el("g", {}, body);
    g.ears = [-1, 1].map(s => el("ellipse", { rx: 0.17, ry: 0.24, fill: o.skinShade }, g.head));
    g.skull = el("path", { d: HEAD, fill: o.skin }, g.head);
    const hclip = el("clipPath", { id: id + "head", clipPathUnits: "userSpaceOnUse" }, defs);
    el("path", { d: HEAD }, hclip);
    g.hairG = el("g", {}, g.head);
    if (HAIRS[o.hair]) el("path", { d: HAIRS[o.hair], fill: o.hairColor, "clip-path": `url(#${id}head)` }, g.hairG);
    g.sprout = o.hair === "tuft" ? el("path", { d: SPROUT, fill: o.hairColor }, g.head) : null;
    g.face = el("g", {}, g.head);
    g.cheeks = [-1, 1].map(s => el("ellipse", { rx: 0.17, ry: 0.1, fill: o.cheek, opacity: 0.32 }, g.face));
    g.nose = el("path", { d: "M 0 0.14 C 0.09 0.14 0.13 0.3 0.11 0.35 C 0.08 0.41 -0.08 0.41 -0.11 0.35 C -0.13 0.3 -0.09 0.14 0 0.14 Z", fill: o.skinShade }, g.face);
    // the mouth: one filled shape whose lower lip drops as it opens, so open and closed never switch elements
    g.mouthG = el("g", {}, g.face);
    const mclip = el("clipPath", { id: id + "mouth", clipPathUnits: "userSpaceOnUse" }, defs);
    g.mclip = el("path", {}, mclip);
    g.mouth = el("path", { fill: o.mouthColor }, g.mouthG);
    g.tongue = el("ellipse", { rx: 0.13, ry: 0.08, fill: o.tongue, "clip-path": `url(#${id}mouth)` }, g.mouthG);
    // eyes: each a group at the eye centre (the screen-right one mirrored, so asymmetric lens shapes pair up). The lens shape is the eye white; with glasses
    // a frame (the same shape, scaled up) sits behind it. Pupil and lid are clipped to the lens
    const eclip = el("clipPath", { id: id + "eye", clipPathUnits: "userSpaceOnUse" }, defs);
    g.eclip = el("path", { d: shapes[0] }, eclip);
    g.bridge = glasses ? el("rect", { x: -0.14, width: 0.28, height: 0.07, rx: 0.035, fill: o.frame }, g.face) : null;
    g.eyes = [-1, 1].map(s => {
      const eg = el("g", {}, g.face);
      const frame = glasses ? el("path", { d: shapes[0], fill: o.frame, transform: `scale(${FRAME})` }, eg) : null;
      const white = el("path", { d: shapes[0], fill: o.white }, eg);
      const inner = el("g", { "clip-path": `url(#${id}eye)` }, eg);
      const pupil = el("circle", { r: 0.12, fill: o.pupilColor }, inner);
      const glint = el("circle", { r: 0.035, fill: o.white }, inner);
      const lid = el("rect", { x: -0.6, width: 1.2, height: 1.2, fill: o.skin }, inner);
      return { s, eg, frame, white, pupil, glint, lid };
    });
    g.brows = [-1, 1].map(s => el("path", { d: BROW, fill: o.hairColor }, g.face));
    const lensD = v => {
      const n = shapes.length; if (n === 1) return shapes[0];
      v = Math.max(0, Math.min(n - 1, v));
      const i = Math.min(n - 2, Math.floor(v)), fr = v - i;
      if (fr < 1e-6) return shapes[i];
      // the lens morph: a plain lerp of the path numbers, which the shared structure of CU.lens shapes makes exact
      const A = nums(shapes[i]), B = nums(shapes[i + 1]); let k = 0;
      if (shape(shapes[i]) !== shape(shapes[i + 1])) return fr < 0.5 ? shapes[i] : shapes[i + 1];
      return shapes[i].replace(NUM, () => { const r = f4(lerp(A[k], B[k], fr)); k++; return r; });
    };
    return { root, body, front, edge, g, o, shapes, lensD, last: {} };
  };

  // ── place and pose ───────────────────────────────────────
  // (x, y): the middle of the edge the character peeks over; h: the head's height in px. Returns the head centre and the
  // two hands' grip points in px, for props or a speech bubble.
  CU.place = (fig, x, y, h, pose, opts = {}) => {
    const p = resolve(pose), g = fig.g, o = fig.o, u = h / 2;
    const turn = { left: -1, right: 1 }[opts.facing || o.facing] || 0;
    fig.edge.setAttribute("y", f4(y - 1e5));
    const T = `translate(${f4(x)} ${f4(y)}) scale(${f4(u)})`;
    fig.body.setAttribute("transform", T); fig.front.setAttribute("transform", T);
    // the head: rise moves it from fully below the edge, sprout and all (centre 1.75), to up (centre -0.85, the chin just
    // under the edge)
    const hy = lerp(1.75, -0.85, p.rise), hx = p.slide * 2;
    const sq = p.squash, sx = 1 + 0.16 * sq, sy = 1 - 0.16 * sq;
    // tilt about the chin, squash about the bottom of the head, so the head stays planted on the edge
    g.head.setAttribute("transform", `translate(${f4(hx)} ${f4(hy + 1)}) rotate(${f4(p.tilt)}) scale(${f4(sx)} ${f4(sy)}) translate(0 -1)`);
    g.ears.forEach((e, i) => { const s = i ? 1 : -1; e.setAttribute("cx", f4(s * 1.1 - turn * 0.1)); e.setAttribute("cy", "0.12"); });
    if (g.sprout) g.sprout.setAttribute("transform", `rotate(${f4(-p.tilt * 0.6 - sq * 18)} 0.12 -0.92)`);
    // the face turns toward `facing` and follows the look a little
    const fx = turn * 0.12 + p.look * 0.04, fy = p.lookY * 0.03;
    g.face.setAttribute("transform", `translate(${f4(fx)} ${f4(fy)})`);
    g.cheeks.forEach((c, i) => { const s = i ? 1 : -1; c.setAttribute("cx", f4(s * 0.72)); c.setAttribute("cy", "0.44"); c.setAttribute("opacity", f4(0.28 + 0.12 * Math.max(0, p.mouth))); });
    // eyes and glasses
    const d = fig.lensD(p.lens);
    if (fig.last.d !== d) {
      g.eclip.setAttribute("d", d);
      g.eyes.forEach(e => { e.white.setAttribute("d", d); if (e.frame) e.frame.setAttribute("d", d); });
      fig.last.d = d; fig.last.top = minY(d);
    }
    const top = fig.last.top, lift = 0.05 * Math.max(0, p.brow) + 0.07 * p.open;
    g.eyes.forEach(e => {
      e.eg.setAttribute("transform", `translate(${f4(e.s * EYE_X)} ${EYE_Y}) scale(${e.s * -1} 1)`);
      // the screen-right eye group is mirrored: its local x is -screen x, hence the -s
      const px = -e.s * p.look * 0.13, py = p.lookY * 0.1, r = 0.12 * p.pupil;
      e.pupil.setAttribute("cx", f4(px)); e.pupil.setAttribute("cy", f4(py)); e.pupil.setAttribute("r", f4(r));
      e.glint.setAttribute("cx", f4(px + e.s * r * 0.35)); e.glint.setAttribute("cy", f4(py - r * 0.4)); e.glint.setAttribute("r", f4(Math.max(0.02, r * 0.3)));
      // the lid comes down from above the lens to past its bottom
      e.lid.setAttribute("y", f4(lerp(top - 1.25, 0.42 - 1.2, p.blink)));
    });
    if (g.bridge) g.bridge.setAttribute("y", f4(EYE_Y - 0.05));
    // brows sit above the glasses' current top and tilt: + worried (inner ends up), - angry (inner ends down)
    const by = Math.max(-0.76, Math.min(-0.5, EYE_Y + top * (g.bridge ? FRAME : 1) - 0.09) - lift);   // never up into the hair
    g.brows.forEach((b, i) => { const s = i ? 1 : -1;
      b.setAttribute("transform", `translate(${f4(s * EYE_X)} ${f4(by - Math.abs(p.brow) * 0.02)}) rotate(${f4(s * p.brow * 16)})`); });
    // mouth: a curved bar; the lower lip drops with open
    const mx = 0, my = 0.6, mw = 0.2 + 0.04 * p.open, c = p.mouth * 0.11, op = p.open * 0.22, th = 0.065;
    const md = `M ${f4(mx - mw)} ${f4(my - c * 0.5)} Q ${f4(mx)} ${f4(my + c * 1.5 - op * 0.3)} ${f4(mx + mw)} ${f4(my - c * 0.5)} ` +
               `Q ${f4(mx + mw + th * 0.5)} ${f4(my - c * 0.5 + th * 0.5)} ${f4(mx + mw)} ${f4(my - c * 0.5 + th)} ` +
               `Q ${f4(mx)} ${f4(my + c * 1.5 + th + op * 1.6)} ${f4(mx - mw)} ${f4(my - c * 0.5 + th)} ` +
               `Q ${f4(mx - mw - th * 0.5)} ${f4(my - c * 0.5 + th * 0.5)} ${f4(mx - mw)} ${f4(my - c * 0.5)} Z`;
    g.mouth.setAttribute("d", md); g.mclip.setAttribute("d", md);
    g.tongue.setAttribute("cx", "0"); g.tongue.setAttribute("cy", f4(my + c * 0.7 + th + op * 0.9 + 0.06));
    // hands: each rises from below the edge; grip hangs the fingertips over it (they grow down from the edge line)
    const hand = (H, s, rise, dx, grip) => {
      const hxp = s * (1.48 + dx), top = lerp(1.0, -0.3, rise);
      H.hg.setAttribute("transform", `translate(${f4(hxp)} ${f4(top + 0.42)}) rotate(${f4(s * -6 * (1 - grip))})`);
      const gr = grip * smooth(prog(rise, 0.85, 1));   // only once the palm shows over the edge
      H.tips.setAttribute("transform", `translate(${f4(hxp)} 0) scale(1 ${f4(gr)})`);
      H.fg.style.display = gr < 1e-4 ? "none" : "";
      return [x + hxp * u, y];
    };
    const L = hand(g.hl, -1, p.hlRise, p.hlX, p.hlGrip), R = hand(g.hr, 1, p.hrRise, p.hrX, p.hrGrip);
    return { head: [x + hx * u, y + hy * u], hands: [L, R] };
  };
})();

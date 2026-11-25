// motion-studio 2.5D sets: paper-cutout characters drawn in code, standing in toon-lit 3D sets built from primitives,
// shot by a camera that moves on the narrator's words. Load after kit.js, figure.js and three.min.js (three.js r159,
// MIT, vendored as scripts/three.min.js with its LICENSE beside it).
//
// The genre (2D cutouts in simple 3D rooms) is reverse-engineered from a public description of how such videos are made
// (an X article on 2.5D stickman channels, 2026-11-24); no code, character or look was copied from it. Its kit shipped
// with no licence, so nothing of it is used. The cast, palette and sets here are motion-studio's own.
//
// Everything is a pure function of t, like every other scene: S3.build(s, opts) makes a scene once, S3.draw(scene, t)
// poses the cast, moves the camera and renders into the segment's own canvas. One WebGL renderer is shared by every
// set3d segment in the film (browsers cap live WebGL contexts at about 16).
//
// Units are metres, y up; the set's floor is y = 0, the camera looks down -z by default. Poses are figure.js poses
// (F.POSES names or literal objects of joint angles), keyed with F.at, so pose timing, anticipation and overshoot behave
// exactly as on a stage. Cutouts are drawn side-on (profile or three-quarter), facing left or right, like stage figures.
(function () {
  const K = window.K, F = window.F;
  const S3 = window.S3 = {};
  if (!window.THREE) { S3.missing = true; return; }
  const T = window.THREE, D = Math.PI / 180;

  // ── palette (the pack's own; script.json "set3d": {"palette": {...}} overrides any key) ─────────────────────────
  S3.PALETTE = {
    ink: "#232733", paper: "#f6eedd", floor: "#d8c4a0", floorAlt: "#cbb38c", wall: "#8fb5ad", wallAlt: "#e7b54f",
    trim: "#f3e6c8", wood: "#a2683f", woodDark: "#7c4c2d", sky: "#bcdbe4", glass: "#d9eef2", leaf: "#4f8a5b",
    leafAlt: "#6aa36f", pot: "#c9623f", metal: "#9aa3ad", road: "#5d6270", line: "#efe3c2", brick: "#c77b57",
    brickAlt: "#b5694a", roof: "#5b6f8f", lamp: "#ffe3a3", rug: "#9c8cc7", book1: "#d9573b", book2: "#3f7f9a",
    book3: "#e7b54f", screen: "#2f3b4d", cloud: "#ffffff", car: "#d9573b", carAlt: "#3f7f9a",
  };
  // ── the cast: motion-studio's own paper dolls. Chunky, big-headed, a cream paper border round the whole figure and an
  // ink line round every part, so they read as cut paper standing in a lit model set. "back": true turns any of them away
  // from the camera (a faceless hero, Shoestring's rule) ─────────────────────────────────────────────────────────────
  S3.LOOKS = {
    ada: { skin: "#f0c9a6", hair: "#3b2a22", hairStyle: "bun", shirt: "#3f8f86", pants: "#2f3b4d", shoes: "#232733" },
    bo:  { skin: "#c98f66", hair: "#232733", hairStyle: "cap", cap: "#e7b54f", shirt: "#e7b54f", pants: "#4b5d7a", shoes: "#5b3a2a" },
    cy:  { skin: "#8d5b3c", hair: "#1d1a17", hairStyle: "curls", shirt: "#d9573b", pants: "#3a3f4b", shoes: "#232733" },
    dee: { skin: "#f3d4b8", hair: "#a8763f", hairStyle: "bob", shirt: "#9c8cc7", pants: "#5d6270", shoes: "#7c4c2d" },
    eli: { skin: "#e0ac86", hair: "#6b4a2e", hairStyle: "crop", shirt: "#f6eedd", pants: "#3f7f9a", shoes: "#232733", tie: "#d9573b" },
  };

  // ── cutout drawing (2D canvas). Figure units are px on a 512 x 768 canvas; the feet stand on y = GROUND ──────────────
  const CW = 512, CH = 768, GROUND = 744, HEAD = 62, TORSO = 150, UARM = 74, FARM = 70, THIGH = 92, SHIN = 92;
  S3.FIG_H = 1.75;                                   // metres from sole to crown of a standing cutout
  const PX_H = 2 * HEAD + 10 + TORSO + THIGH + SHIN + 14;   // crown to sole in px when standing straight
  const PLANE_H = CH / PX_H * S3.FIG_H, PLANE_W = PLANE_H * CW / CH;
  const shade = (hex, k) => { const n = parseInt(hex.slice(1), 16), c = [n >> 16, (n >> 8) & 255, n & 255].map(v => Math.round(Math.max(0, Math.min(255, v * k))));
    return "#" + c.map(v => v.toString(16).padStart(2, "0")).join(""); };
  const dir = a => [Math.sin(a * D), Math.cos(a * D)];      // 0 = straight down, positive toward the facing side

  function capsule(ctx, pts, w, fill, ink, paper, pass) {
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    if (pass === 0) { ctx.strokeStyle = paper; ctx.lineWidth = w + 30; ctx.stroke(); return; }
    ctx.strokeStyle = ink; ctx.lineWidth = w + 9; ctx.stroke(); ctx.strokeStyle = fill; ctx.lineWidth = w; ctx.stroke();
  }
  function blob(ctx, path, fill, ink, paper, pass, lw = 4.5) {
    if (pass === 0) { ctx.strokeStyle = paper; ctx.lineWidth = 30; ctx.lineJoin = "round"; ctx.stroke(path); ctx.fillStyle = paper; ctx.fill(path); return; }
    ctx.fillStyle = fill; ctx.fill(path); ctx.strokeStyle = ink; ctx.lineWidth = lw; ctx.lineJoin = "round"; ctx.stroke(path);
  }
  function circle(x, y, r) { const p = new Path2D(); p.arc(x, y, r, 0, Math.PI * 2); return p; }

  // the hair, cap and back of head, drawn on a head of radius HEAD centred at (hx, hy), facing f (+1 right, -1 left)
  function hair(ctx, L, hx, hy, f, back, ink, paper, pass) {
    const R = HEAD, p = new Path2D(), X = x => hx + x * R * f, Y = y => hy + y * R;
    const st = L.hairStyle;
    if (back) { p.arc(hx, hy, R * 1.02, 0, Math.PI * 2); }
    else if (st === "bun") { p.moveTo(X(-1.02), Y(0.2)); p.bezierCurveTo(X(-1.15), Y(-0.9), X(-0.2), Y(-1.3), X(0.45), Y(-1.08));
      p.bezierCurveTo(X(0.95), Y(-0.85), X(1.05), Y(-0.45), X(1.0), Y(-0.3)); p.bezierCurveTo(X(0.5), Y(-0.62), X(-0.1), Y(-0.55), X(-0.45), Y(-0.2));
      p.bezierCurveTo(X(-0.62), Y(0.05), X(-0.78), Y(0.25), X(-1.02), Y(0.2)); p.closePath();
      p.moveTo(X(-0.55) + R * 0.42, Y(-1.1)); p.arc(X(-0.55), Y(-1.1), R * 0.42, 0, Math.PI * 2); }
    else if (st === "cap") { p.moveTo(X(-1.04), Y(-0.15)); p.bezierCurveTo(X(-1.05), Y(-1.15), X(0.9), Y(-1.2), X(1.0), Y(-0.3));
      p.lineTo(X(1.55), Y(-0.22)); p.bezierCurveTo(X(1.5), Y(-0.08), X(0.7), Y(-0.08), X(-1.04), Y(-0.15)); p.closePath(); }
    else if (st === "curls") { for (let i = 0; i < 9; i++) { const a = Math.PI * (1.0 + i / 8 * 0.95), r = R * 0.36;
        const cx = hx + Math.cos(a) * R * 0.92 * f * (i < 4 ? 1.05 : 1), cy = hy + Math.sin(a) * R * 0.92 - 2; p.moveTo(cx + r, cy); p.arc(cx, cy, r, 0, Math.PI * 2); }
      p.moveTo(X(-1.05), Y(0.3)); p.arc(X(-0.95), Y(0.25), R * 0.33, 0, Math.PI * 2); }
    else if (st === "bob") { p.moveTo(X(-1.1), Y(0.75)); p.bezierCurveTo(X(-1.35), Y(-0.6), X(-0.6), Y(-1.3), X(0.25), Y(-1.18));
      p.bezierCurveTo(X(1.0), Y(-1.05), X(1.18), Y(-0.45), X(1.06), Y(-0.05)); p.bezierCurveTo(X(0.55), Y(-0.6), X(0.0), Y(-0.55), X(-0.38), Y(-0.38));
      p.bezierCurveTo(X(-0.5), Y(0.15), X(-0.42), Y(0.55), X(-0.3), Y(0.82)); p.bezierCurveTo(X(-0.6), Y(0.92), X(-0.95), Y(0.9), X(-1.1), Y(0.75)); p.closePath(); }
    else if (st === "crop") { p.moveTo(X(-1.03), Y(0.0)); p.bezierCurveTo(X(-1.1), Y(-1.0), X(0.1), Y(-1.32), X(0.75), Y(-0.95));
      p.bezierCurveTo(X(1.02), Y(-0.75), X(1.05), Y(-0.5), X(0.98), Y(-0.42)); p.bezierCurveTo(X(0.4), Y(-0.72), X(-0.2), Y(-0.6), X(-0.5), Y(-0.3));
      p.bezierCurveTo(X(-0.7), Y(-0.1), X(-0.85), Y(0.05), X(-1.03), Y(0.0)); p.closePath(); }
    else return;
    blob(ctx, p, st === "cap" && !back ? L.cap : L.hair, ink, paper, pass);
    if (st === "cap" && back) {      // from behind, the cap's crown and its back strap, so a capped head isn't a plain ball
      const c = new Path2D(); c.moveTo(hx - R * 1.04, hy - R * 0.12); c.bezierCurveTo(hx - R * 1.05, hy - R * 1.25, hx + R * 1.05, hy - R * 1.25, hx + R * 1.04, hy - R * 0.12);
      c.bezierCurveTo(hx + R * 0.4, hy - R * 0.3, hx - R * 0.4, hy - R * 0.3, hx - R * 1.04, hy - R * 0.12); c.closePath();
      blob(ctx, c, L.cap, ink, paper, pass);
      if (pass === 1) { ctx.fillStyle = ink; ctx.beginPath(); ctx.ellipse(hx, hy - R * 0.3, R * 0.28, R * 0.09, 0, 0, Math.PI * 2); ctx.fill(); }
    }
    if (st === "cap" && !back && pass === 1) {      // the hair under the cap at the nape
      const n = new Path2D(); n.moveTo(X(-1.0), Y(-0.1)); n.bezierCurveTo(X(-1.08), Y(0.25), X(-0.9), Y(0.45), X(-0.7), Y(0.4)); n.lineTo(X(-0.62), Y(-0.1)); n.closePath();
      blob(ctx, n, L.hair, ink, paper, 1, 3);
    }
  }

  // one frame of one cutout: pose p (a figure.js pose), facing f, `talk` 0..1 opens the mouth
  S3.RES = 2;      // texture pixels per figure unit: close shots fill the frame with a head, so the paper is drawn at 2x
  S3.drawCutout = (ctx, L, p, f = 1, o = {}) => {
    const ink = o.ink || S3.PALETTE.ink, paper = o.paper || S3.PALETTE.paper, back = !!o.back, k = ctx.canvas.width / CW;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height); ctx.setTransform(k, 0, 0, k, 0, 0);
    const cx = CW / 2, crouch = (p.crouch || 0) * (THIGH + SHIN) * 0.55, bob = (p.bob || 0) * 3;
    // legs first, to find where the feet land: the lowest foot stands on the ground
    const leg = (h, k) => { const a = dir(h), b = dir(h + k); return [[0, 0], [a[0] * THIGH * f, a[1] * THIGH], [a[0] * THIGH * f + b[0] * SHIN * f, a[1] * THIGH + b[1] * SHIN]]; };
    const LL = leg(p.lh, p.lk), RL = leg(p.rh, p.rk), drop = Math.max(LL[2][1], RL[2][1]);
    const hip = [cx - 6 * f, GROUND - 12 - drop - bob + crouch * 0];
    const off = q => q.map(([x, y]) => [hip[0] + x, hip[1] + y]);
    const lleg = off(LL), rleg = off(RL);
    const lean = (p.lean || 0) * D, sh = [hip[0] + Math.sin(lean) * TORSO * f, hip[1] - Math.cos(lean) * TORSO];
    const hl = lean + (p.head || 0) * D, neck = [sh[0] + Math.sin(lean) * 10 * f, sh[1] - Math.cos(lean) * 10];
    const hc = [neck[0] + Math.sin(hl) * (HEAD - 4) * f, neck[1] - Math.cos(hl) * (HEAD - 4)];
    const arm = (s, e) => { const a = dir(s), b = dir(s + e); const sp = [sh[0] - 4 * f, sh[1] + 16];
      return [sp, [sp[0] + a[0] * UARM * f, sp[1] + a[1] * UARM], [sp[0] + a[0] * UARM * f + b[0] * FARM * f, sp[1] + a[1] * UARM + b[1] * FARM]]; };
    const larm = arm(p.ls, p.le), rarm = arm(p.rs, p.re);
    const torso = new Path2D(); {
      const w0 = 30, w1 = 44, ux = Math.cos(lean) * f, uy = Math.sin(lean);      // across the torso
      torso.moveTo(hip[0] - ux * w0, hip[1] - uy * w0 * f + 6); torso.lineTo(sh[0] - ux * w1, sh[1] - uy * w1 * f + 12);
      torso.quadraticCurveTo(sh[0], sh[1] - 14, sh[0] + ux * w1, sh[1] + uy * w1 * f + 12); torso.lineTo(hip[0] + ux * w0, hip[1] + uy * w0 * f + 6);
      torso.quadraticCurveTo(hip[0], hip[1] + 22, hip[0] - ux * w0, hip[1] - uy * w0 * f + 6); torso.closePath(); }
    const far = (c) => shade(c, 0.82);
    const shoe = (q, c, pass) => { const [x, y] = q[2], s = new Path2D(); s.ellipse(x + 10 * f, y + 4, 22, 11, 0, 0, Math.PI * 2); blob(ctx, s, c, ink, paper, pass); };
    const hand = (q, pass, c) => blob(ctx, circle(q[2][0], q[2][1], 13), c, ink, paper, pass);
    for (const pass of [0, 1]) {
      // far side (behind the torso), a shade darker
      capsule(ctx, lleg, 30, far(L.pants), ink, paper, pass); shoe(lleg, far(L.shoes), pass);
      capsule(ctx, larm.slice(0, 2), 26, far(L.shirt), ink, paper, pass); capsule(ctx, larm.slice(1), 22, far(L.skin), ink, paper, pass); hand(larm, pass, far(L.skin));
      capsule(ctx, rleg, 30, L.pants, ink, paper, pass); shoe(rleg, L.shoes, pass);
      blob(ctx, torso, L.shirt, ink, paper, pass);
      if (L.tie && !back && pass === 1) { const tp = new Path2D(); const tx = sh[0] + 14 * f, ty = sh[1] + 6;
        tp.moveTo(tx - 6, ty); tp.lineTo(tx + 6, ty); tp.lineTo(tx + 8, ty + 70); tp.lineTo(tx, ty + 82); tp.lineTo(tx - 8, ty + 70); tp.closePath(); blob(ctx, tp, L.tie, ink, paper, 1, 3); }
      capsule(ctx, [neck, [neck[0], neck[1] + 4]], 22, L.skin, ink, paper, pass);
      blob(ctx, circle(hc[0], hc[1], HEAD), back ? L.hair : L.skin, ink, paper, pass);
      hair(ctx, L, hc[0], hc[1], f, back, ink, paper, pass);
      if (!back) { const ear = circle(hc[0] - 0.5 * HEAD * f, hc[1] + 0.12 * HEAD, 11); blob(ctx, ear, L.skin, ink, paper, pass, 3.5); }
      capsule(ctx, rarm.slice(0, 2), 26, L.shirt, ink, paper, pass); capsule(ctx, rarm.slice(1), 22, L.skin, ink, paper, pass); hand(rarm, pass, L.skin);
    }
    if (back) return { hip, head: hc };
    // the face: a three-quarter view toward the facing side. Eyes are tall ovals that squash to a line on a blink and
    // follow `look`; brows tilt from angry (-1) to worried (1); the mouth curves from frown (-1) to smile (1) and opens
    const R = HEAD, lk = (p.look || 0) * 4, ly = (p.lookY || 0) * 4, bl = 1 - Math.min(1, (p.blink || 0));
    const eye = (ex, ey, r) => { ctx.fillStyle = ink; ctx.beginPath(); ctx.ellipse(ex + lk * f, ey + ly, r * 0.62, Math.max(1.6, r * bl), 0, 0, Math.PI * 2); ctx.fill(); };
    const e1 = [hc[0] + 0.42 * R * f, hc[1] - 0.06 * R], e2 = [hc[0] + 0.02 * R * f, hc[1] - 0.06 * R];
    eye(e1[0], e1[1], 9.5); eye(e2[0], e2[1], 8.5);
    ctx.strokeStyle = ink; ctx.lineWidth = 5; ctx.lineCap = "round";
    const brow = (bx, by) => { const b = p.brow || 0; ctx.beginPath(); ctx.moveTo(bx - 11 * f, by + b * 5); ctx.lineTo(bx + 11 * f, by - b * 5); ctx.stroke(); };
    brow(e1[0], e1[1] - 22); brow(e2[0], e2[1] - 21);
    const m = p.mouth || 0, op = Math.max(p.open || 0, o.talk || 0), mx = hc[0] + 0.3 * R * f, my = hc[1] + 0.42 * R;
    if (op > 0.08) { ctx.fillStyle = shade(ink, 1.6); ctx.beginPath(); ctx.ellipse(mx, my + 2, 10 + 4 * Math.abs(m), 3 + 13 * op, 0, 0, Math.PI * 2); ctx.fill();
      ctx.lineWidth = 4; ctx.stroke(); }
    else { ctx.lineWidth = 5; ctx.beginPath(); ctx.moveTo(mx - 13, my - m * 3); ctx.quadraticCurveTo(mx, my + m * 9, mx + 13, my - m * 3); ctx.stroke(); }
    // cheek
    ctx.fillStyle = "rgba(217,87,59,0.22)"; ctx.beginPath(); ctx.ellipse(hc[0] + 0.5 * R * f, hc[1] + 0.25 * R, 11, 7, 0, 0, Math.PI * 2); ctx.fill();
    return { hip, head: hc };
  };
  S3.CANVAS = [CW, CH]; S3.PLANE = [PLANE_W, PLANE_H];

  // ── toon materials and ink outlines ──────────────────────────────────────────────────────────────────────────────
  let GRAD = null;
  const gradient = () => GRAD || (GRAD = (() => { const d = new Uint8Array([110, 180, 255]);
    const g = new T.DataTexture(d, 3, 1, T.RedFormat); g.minFilter = g.magFilter = T.NearestFilter; g.needsUpdate = true; return g; })());
  const mats = new Map();
  const toon = (c, emissive) => { const k = c + (emissive ? "e" : ""); if (!mats.has(k)) mats.set(k, emissive
      ? new T.MeshBasicMaterial({ color: new T.Color(c) }) : new T.MeshToonMaterial({ color: new T.Color(c), gradientMap: gradient() })); return mats.get(k); };
  let INK_MAT = null;
  const inkMat = ink => INK_MAT || (INK_MAT = new T.MeshBasicMaterial({ color: new T.Color(ink), side: T.BackSide }));
  const OUT = 0.022;     // outline thickness, metres: an inverted hull, scaled per axis so the line is even on every face

  // a set piece: {shape: box|cyl|cone|sphere|plane|disc, at: [x, y, z] (base centre for box/cyl/cone), size: [w, h, d]
  // (cyl/cone: [diameter, h]; sphere: [diameter]; plane: [w, h], disc: [diameter]), color: palette key or #hex,
  // rot: degrees about y (or [x, y, z]), outline: false, glow: true (unlit, for lamps and screens), shadow: false,
  // sway: degrees (rocks gently about its base), spin: degrees a second about y, drift: [vx, vz] m/s with wrap: [min, max] on x}
  function piece(P, pal, ink) {
    const col = pal[P.color] || P.color || pal.wall, sz = P.size || [1, 1, 1];
    let geo, scaleOut;
    switch (P.shape || "box") {
      case "cyl": geo = new T.CylinderGeometry(sz[0] / 2, (sz[2] || sz[0]) / 2, sz[1], 28); geo.translate(0, sz[1] / 2, 0);
        scaleOut = [(sz[0] + 2 * OUT) / sz[0], (sz[1] + 2 * OUT) / sz[1], (sz[0] + 2 * OUT) / sz[0]]; break;
      case "cone": geo = new T.ConeGeometry(sz[0] / 2, sz[1], 28); geo.translate(0, sz[1] / 2, 0);
        scaleOut = [(sz[0] + 2 * OUT) / sz[0], (sz[1] + 2 * OUT) / sz[1], (sz[0] + 2 * OUT) / sz[0]]; break;
      case "sphere": geo = new T.SphereGeometry(sz[0] / 2, 28, 18); geo.translate(0, sz[0] / 2, 0);
        scaleOut = Array(3).fill((sz[0] + 2 * OUT) / sz[0]); break;
      case "plane": geo = new T.PlaneGeometry(sz[0], sz[1]); geo.translate(0, sz[1] / 2, 0); scaleOut = null; break;
      case "disc": geo = new T.CircleGeometry(sz[0] / 2, 40); geo.rotateX(-Math.PI / 2); scaleOut = null; break;
      default: geo = new T.BoxGeometry(sz[0], sz[1], sz[2]); geo.translate(0, sz[1] / 2, 0);
        scaleOut = [(sz[0] + 2 * OUT) / sz[0], (sz[1] + 2 * OUT) / sz[1], (sz[2] + 2 * OUT) / sz[2]];
    }
    const g = new T.Group(), mesh = new T.Mesh(geo, toon(col, P.glow));
    mesh.castShadow = P.shadow !== false && !P.glow; mesh.receiveShadow = true; g.add(mesh);
    if (scaleOut && P.outline !== false) { const o = new T.Mesh(geo, inkMat(ink)); o.scale.set(...scaleOut);
      o.position.y = -OUT * (P.shape === "sphere" ? 1 : 1); g.add(o); }
    const at = P.at || [0, 0, 0]; g.position.set(at[0], at[1] || 0, at[2] || 0);
    const r = Array.isArray(P.rot) ? P.rot : [0, P.rot || 0, 0]; g.rotation.set(r[0] * D, r[1] * D, r[2] * D);
    g.userData = { P, base: g.rotation.clone(), at: at.slice() };
    return g;
  }

  // ── preset sets, all from primitives. A segment's data.pieces add to (or with data.set: "none", replace) them ─────
  const books = (x, y, z, n, seed) => { const r = K.rng(seed), out = []; let bx = x;
    for (let i = 0; i < n; i++) { const w = 0.05 + r() * 0.04, h = 0.2 + r() * 0.1; out.push({ shape: "box", at: [bx + w / 2, y, z], size: [w, h, 0.18], color: ["book1", "book2", "book3"][i % 3] }); bx += w + 0.01; }
    return out; };
  S3.SETS = {
    room: () => [
      { shape: "box", at: [0, -0.1, 0], size: [12, 0.1, 9], color: "floor", outline: false },
      { shape: "box", at: [0, 0, -3.2], size: [12, 5, 0.2], color: "wall", outline: false },
      { shape: "box", at: [-4.6, 0, 0], size: [0.2, 5, 9], color: "wallAlt", outline: false },
      { shape: "box", at: [0, 0, -3.08], size: [12, 0.16, 0.06], color: "trim" },
      { shape: "box", at: [1.6, 1.2, -3.12], size: [1.9, 1.5, 0.08], color: "trim" },
      { shape: "plane", at: [1.6, 1.3, -3.07], size: [1.6, 1.3], color: "sky", glow: true, outline: false },
      { shape: "box", at: [1.6, 1.2, -3.06], size: [0.06, 1.5, 0.06], color: "trim" },
      { shape: "sphere", at: [1.2, 1.95, -3.2], size: [0.34], color: "cloud", glow: true, outline: false, drift: [0.06, 0], wrap: [0.8, 2.4], shadow: false },
      { shape: "disc", at: [0, 0.005, -0.4], size: [3.2], color: "rug", outline: false },
      { shape: "box", at: [-2.4, 0.72, -2.2], size: [1.8, 0.07, 0.8], color: "wood" },
      ...[[-3.2, -2.5], [-1.6, -2.5], [-3.2, -1.9], [-1.6, -1.9]].map(([x, z]) => ({ shape: "box", at: [x, 0, z], size: [0.07, 0.72, 0.07], color: "woodDark" })),
      { shape: "box", at: [-2.4, 0.79, -2.35], size: [0.6, 0.38, 0.04], color: "screen", glow: true },
      { shape: "box", at: [-2.4, 0.79, -2.32], size: [0.08, 0.02, 0.08], color: "metal" },
      { shape: "box", at: [3.4, 1.6, -3.0], size: [1.4, 0.05, 0.3], color: "wood" },
      ...books(2.8, 1.65, -3.0, 9, 3),
      { shape: "cyl", at: [-3.9, 0, -2.6], size: [0.42, 0.45], color: "pot" },
      { shape: "sphere", at: [-3.9, 0.4, -2.6], size: [0.75], color: "leaf", sway: 2 },
      { shape: "sphere", at: [-3.75, 0.85, -2.55], size: [0.45], color: "leafAlt", sway: 3 },
      { shape: "cyl", at: [4.3, 0, -2.4], size: [0.05, 1.5], color: "metal" },
      { shape: "cone", at: [4.3, 1.45, -2.4], size: [0.5, 0.35], color: "lamp", glow: true },
    ],
    street: () => [
      { shape: "box", at: [0, -0.1, 0], size: [30, 0.1, 14], color: "road", outline: false },
      { shape: "box", at: [0, 0, -3.0], size: [30, 0.14, 3], color: "floor" },
      ...Array.from({ length: 8 }, (_, i) => ({ shape: "box", at: [-13 + i * 3.6, 0.005, 1.0], size: [1.6, 0.01, 0.14], color: "line", outline: false, shadow: false })),
      ...[[-8, 5.5, "brick"], [-4.2, 7, "wall"], [-0.4, 4.5, "brickAlt"], [3.4, 6.2, "wallAlt"], [7.2, 5, "brick"], [11, 6.8, "wall"]].flatMap(([x, h, c], i) => [
        { shape: "box", at: [x, 0, -6], size: [3.6, h, 3], color: c },
        { shape: "box", at: [x, h, -6], size: [3.8, 0.25, 3.2], color: "roof" },
        ...Array.from({ length: Math.floor(h / 1.6) * 2 }, (_, k) => ({ shape: "box", at: [x - 0.8 + (k % 2) * 1.6, 1.0 + Math.floor(k / 2) * 1.6, -4.47], size: [0.8, 0.9, 0.06], color: (k + i) % 5 === 0 ? "lamp" : "glass", glow: (k + i) % 5 === 0 })),
      ]),
      ...[-6, 1.5, 9].map(x => ({ shape: "cyl", at: [x, 0.14, -1.8], size: [0.1, 3], color: "ink" })),
      ...[-6, 1.5, 9].map(x => ({ shape: "sphere", at: [x, 2.95, -1.8], size: [0.36], color: "lamp", glow: true })),
      { shape: "cyl", at: [-2.2, 0.14, -2.4], size: [0.24, 1.6], color: "woodDark" },
      { shape: "sphere", at: [-2.2, 1.4, -2.4], size: [1.5], color: "leaf", sway: 2 },
      { shape: "sphere", at: [-2.0, 2.3, -2.3], size: [0.9], color: "leafAlt", sway: 3 },
      ...[0, 1].map(k => ({ shape: "sphere", at: [-10 + k * 9, 9 + k, -14], size: [2.2 - k * 0.5], color: "cloud", glow: true, outline: false, shadow: false, drift: [0.25, 0], wrap: [-16, 16] })),
      // a car on the far lane, behind where the cast stands (z 0 to 1.5), so it never drives through anyone or the lens
      { shape: "box", at: [-12, 0.25, -0.75], size: [3.4, 0.9, 1.4], color: "car", drift: [1.8, 0], wrap: [-18, 18] },
      { shape: "box", at: [-12.2, 1.15, -0.75], size: [1.9, 0.6, 1.25], color: "glass", drift: [1.8, 0], wrap: [-18, 18] },
      ...[-13.1, -10.9].map(x => ({ shape: "cyl", at: [x, 0, 0.0], size: [0.62, 0.22], rot: [90, 0, 0], color: "ink", drift: [1.8, 0], wrap: [-18, 18] })),
    ],
    stage: () => [
      { shape: "box", at: [0, -0.1, 0], size: [14, 0.1, 10], color: "floor", outline: false },
      { shape: "box", at: [0, 0, -3.4], size: [14, 6, 0.2], color: "wall", outline: false },
      { shape: "disc", at: [0, 0.005, -0.3], size: [4.2], color: "floorAlt", outline: false },
    ],
    none: () => [],
  };

  // ── camera: keys {t, pos: [x,y,z], look: [x,y,z], fov?, cut?: true, move?: seconds} or a named shot on a cast member
  // {t, shot: "wide"|"med"|"close"|"low"|"high"|"over", on: "<cast id>", side?: -1|1}. Between keys the camera eases
  // over `move` (0.8 s); a key with cut: true jumps. Over every hold the camera drifts on (a slow dolly toward what it
  // looks at plus a small sideways truck, data.drift metres a second, 0.06 by default), so the picture never freezes ──
  // [sideways offset, camera height, distance, look height] per shot, in metres from what it frames
  const SHOTS = { wide: [0, 1.6, 6.8, 1.0], med: [0.4, 1.45, 4.2, 1.15], close: [0.3, 1.5, 2.7, 1.42], low: [0.9, 0.3, 3.0, 1.55],
                  high: [0.3, 3.8, 4.8, 0.8], over: [-0.9, 1.6, 2.8, 1.35] };
  function camKey(k, cast, fov0 = 35) {
    if (!k.shot) return { pos: new T.Vector3(...(k.pos || [0, 1.7, 7])), look: new T.Vector3(...(k.look || [0, 1.1, 0])), fov: k.fov || fov0 };
    // "on": one cast id, a list of them, or none (the whole cast): the shot frames their centre
    const ids = k.on == null ? Object.keys(cast) : [].concat(k.on), cs = ids.map(i => cast[i]).filter(Boolean);
    const at = cs.length ? cs.reduce((a, c) => a.add(c.posAt(k.t)), new T.Vector3()).multiplyScalar(1 / cs.length) : new T.Vector3();
    const s = SHOTS[k.shot] || SHOTS.med, side = k.side || 1;
    return { pos: new T.Vector3(at.x + s[0] * side, s[1], at.z + s[2]), look: new T.Vector3(at.x, s[3], at.z), fov: k.fov || fov0 };
  }

  // ── build one set3d segment ─────────────────────────────────────────────────────────────────────────────────────
  // data: {set?: "room"|"street"|"stage"|"none", pieces?: [...], palette?: {...}, light?: {az, el, warm},
  //        cast: [{id, look: "ada"|{...}, at: [x, z], face: "right"|"left", back?: true, talks?: true, keys: [{at|t, pose?,
  //        to?: [x, z], walk?: seconds, face?}]}], camera: [...], drift?: 0.06}
  S3.build = (s, opts = {}) => {
    const d = s.data || {}, pal = Object.assign({}, S3.PALETTE, opts.palette || {}, d.palette || {}), ink = pal.ink;
    const scene = new T.Scene(); scene.background = new T.Color(pal.sky);
    const L = d.light || {}, az = (L.az ?? 35) * D, el = (L.el ?? 50) * D;
    scene.add(new T.HemisphereLight(0xffffff, new T.Color(pal.floor).multiplyScalar(0.6), 1.25));
    const sun = new T.DirectionalLight(new T.Color(L.warm || "#fff1dc"), 2.2);
    sun.position.set(Math.sin(az) * Math.cos(el) * 12, Math.sin(el) * 12, Math.cos(az) * Math.cos(el) * 12);
    sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0006; sun.shadow.radius = 3;
    Object.assign(sun.shadow.camera, { left: -9, right: 9, top: 9, bottom: -9, near: 0.5, far: 40 }); scene.add(sun);
    const pieces = [...(S3.SETS[d.set || "room"] || S3.SETS.room)(), ...(d.pieces || [])].map(P => { const g = piece(P, pal, ink); scene.add(g); return g; });
    // the cast: a canvas per cutout, redrawn each frame from its pose (cheap at 512 x 768), on a plane that turns about y
    // to face the camera (the paper always faces the lens, as in a model set shot with cutouts)
    const cast = {};
    (d.cast || []).forEach((c, i) => {
      const look = typeof c.look === "object" ? Object.assign({}, S3.LOOKS.ada, c.look) : (S3.LOOKS[c.look] || Object.values(S3.LOOKS)[i % 5]);
      const cv = document.createElement("canvas"); cv.width = CW * S3.RES; cv.height = CH * S3.RES;
      const tex = new T.CanvasTexture(cv); tex.colorSpace = T.SRGBColorSpace; tex.anisotropy = 4;
      const mat = new T.MeshBasicMaterial({ map: tex, transparent: true, alphaTest: 0.5, side: T.DoubleSide });
      const geo = new T.PlaneGeometry(PLANE_W, PLANE_H); geo.translate(0, PLANE_H / 2 - (CH - GROUND) / CH * PLANE_H, 0);
      const mesh = new T.Mesh(geo, mat); mesh.castShadow = true;
      mesh.customDepthMaterial = new T.MeshDepthMaterial({ depthPacking: T.RGBADepthPacking, map: tex, alphaTest: 0.5 });
      const blobS = new T.Mesh(new T.CircleGeometry(0.42, 32), new T.MeshBasicMaterial({ color: new T.Color(ink), transparent: true, opacity: 0.22, depthWrite: false }));
      blobS.rotation.x = -Math.PI / 2; blobS.scale.set(1, 0.45, 1); blobS.position.y = 0.006;
      const g = new T.Group(); g.add(mesh); g.add(blobS); scene.add(g);
      const keys = (c.keys || []).map(k => Object.assign({}, k, { t: k.t ?? s.t0 })).sort((a, b) => a.t - b.t);
      const poseKeys = [[s.t0 - 1, c.pose || "stand"], ...keys.filter(k => k.pose).map(k => [k.t - 0.05, k.pose])];
      // walks: a key with `to` walks there from where the figure stands, over `walk` s (default: 1.4 m/s)
      const legs = []; let here = c.at || [0, 0];
      keys.filter(k => k.to).forEach(k => { const dist = Math.hypot(k.to[0] - here[0], k.to[1] - here[1]), dur = k.walk || Math.max(0.6, dist / 1.4);
        legs.push({ a: k.t, b: k.t + dur, from: here, to: k.to }); here = k.to; });
      const posAt = t => { let p = c.at || [0, 0];
        for (const l of legs) { if (t >= l.b) p = l.to; else if (t > l.a) { const u = K.eio(K.prog(t, l.a, l.b)); p = [K.lerp(l.from[0], l.to[0], u), K.lerp(l.from[1], l.to[1], u)]; } }
        return new T.Vector3(p[0], 0, p[1]); };
      const faceAt = t => { let f = c.face === "left" ? -1 : 1;
        for (const l of legs) if (t > l.a && Math.abs(l.to[0] - l.from[0]) > 0.05) f = l.to[0] > l.from[0] ? 1 : -1;
        for (const k of keys) if (k.face && t >= k.t) f = k.face === "left" ? -1 : 1;
        return f; };
      cast[c.id || "c" + i] = { c, look, cv, ctx: cv.getContext("2d"), tex, mesh, g, blobS, keys, poseKeys, legs, posAt, faceAt, seed: i * 3 + 1, last: "" };
    });
    const camera = new T.PerspectiveCamera(35, opts.W / opts.H, 0.1, 200);
    const cams = (d.camera && d.camera.length ? d.camera : [{ shot: "wide" }, { t: s.t0 + (s.t1 - s.t0) * 0.5, shot: "med" }])
      .map(k => Object.assign({}, k, { t: k.t ?? s.t0 })).sort((a, b) => a.t - b.t);
    // a vertical frame (a Short) keeps a shot's width: the same 16:9 framing needs a wider vertical angle (35° -> 87°
    // would distort), so 9:16 takes 58° and the shot's subject fills the middle of the frame
    const fov0 = opts.H > opts.W ? 58 : 35;
    // a pose name F.POSES lacks would silently stand still: say so in the page's warnings, which check prints
    (d.cast || []).forEach(c => [c.pose, ...(c.keys || []).map(k => k.pose)].forEach(p => {
      if (typeof p === "string" && !F.POSES[p]) (window.STUDIO.warnings = window.STUDIO.warnings || []).push(`${s.id}: set3d pose "${p}" is not a figure.js pose (${Object.keys(F.POSES).join(", ")})`); }));
    cams.forEach(k => [].concat(k.on ?? []).forEach(id => { if (!cast[id]) (window.STUDIO.warnings = window.STUDIO.warnings || []).push(`${s.id}: set3d camera "on" names "${id}", who is not in the cast (${Object.keys(cast).join(", ")})`); }));
    if (!S3.SETS[d.set || "room"]) (window.STUDIO.warnings = window.STUDIO.warnings || []).push(`${s.id}: set3d set "${d.set}" is not one of ${Object.keys(S3.SETS).join(", ")}`);
    return { s, d, scene, camera, pieces, cast, cams, pal, fov0, drift: d.drift ?? 0.06 };
  };

  function cameraAt(sc, t) {
    const ks = sc.cams; let i = 0;
    while (i + 1 < ks.length && t >= ks[i + 1].t) i++;
    const cur = camKey(ks[i], sc.cast, sc.fov0), prev = i > 0 ? camKey(ks[i - 1], sc.cast, sc.fov0) : cur;
    const mv = ks[i].cut || i === 0 ? 0 : (ks[i].move ?? 0.8), u = mv ? K.eio(K.prog(t, ks[i].t, ks[i].t + mv)) : 1;
    const pos = prev.pos.clone().lerp(cur.pos, u), look = prev.look.clone().lerp(cur.look, u), fov = K.lerp(prev.fov, cur.fov, u);
    // the drift: on from this key's start, a dolly toward the look point and a slow sideways truck
    const held = t - ks[i].t, v = look.clone().sub(pos), dist = v.length(); v.normalize();
    const side = new T.Vector3(-v.z, 0, v.x).normalize(), dir2 = i % 2 ? -1 : 1;
    pos.addScaledVector(v, Math.min(dist * 0.25, sc.drift * held)).addScaledVector(side, dir2 * sc.drift * 0.6 * held);
    return { pos, look, fov };
  }

  let R = null;
  S3.renderer = (W, H) => {
    if (!R) { R = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true, alpha: false });
      R.setPixelRatio(1); R.shadowMap.enabled = true; R.shadowMap.type = T.PCFSoftShadowMap; R.outputColorSpace = T.SRGBColorSpace; }
    if (R.domElement.width !== W || R.domElement.height !== H) R.setSize(W, H, false);
    return R;
  };

  // pose everything at t and render into ctx2d (the segment's own canvas); returns the cast's screen boxes for the
  // pace probe and the camera's screen anchor
  S3.draw = (sc, t, out, W, H, words) => {
    const { scene, camera } = sc;
    sc.pieces.forEach(g => { const P = g.userData.P;
      if (P.sway) g.rotation.z = g.userData.base.z + Math.sin(t * 1.3 + g.userData.at[0]) * P.sway * D;
      if (P.spin) g.rotation.y = g.userData.base.y + t * P.spin * D;
      if (P.drift) { const w = P.wrap || [-15, 15], span = w[1] - w[0]; let x = g.userData.at[0] + P.drift[0] * t;
        x = w[0] + ((((x - w[0]) % span) + span) % span); g.position.x = x; g.position.z = g.userData.at[2] + (P.drift[1] || 0) * t; } });
    const cam = cameraAt(sc, t);
    camera.position.copy(cam.pos); camera.lookAt(cam.look); if (camera.fov !== cam.fov) { camera.fov = cam.fov; camera.updateProjectionMatrix(); }
    const boxes = [];
    Object.values(sc.cast).forEach(m => {
      const p0 = F.at(t, m.poseKeys, { anticipate: true });
      let pose = F.idle(t, m.seed, p0);
      for (const l of m.legs) pose = F.stride(t, l.a, l.b, pose);
      const f = m.faceAt(t);
      let talk = 0;
      if (m.c.talks && words) for (const w of words) if (t >= w[1] && t <= w[2]) { talk = 0.25 + 0.45 * Math.abs(Math.sin((t - w[1]) * 18)); break; }
      S3.drawCutout(m.ctx, m.look, pose, f, { back: m.c.back, talk, ink: sc.pal.ink, paper: sc.pal.paper });
      m.tex.needsUpdate = true;
      const at = m.posAt(t); m.g.position.copy(at);
      m.mesh.rotation.y = Math.atan2(camera.position.x - at.x, camera.position.z - at.z);
      // screen box for the pace probe: the projected crown and sole
      const top = at.clone().setY(S3.FIG_H).project(camera), bot = at.clone().project(camera);
      const h = (top.y - bot.y) / 2 * H, cx = (bot.x + 1) / 2 * W, cy = (1 - top.y) / 2 * H;
      let key = "stand"; for (const pk of m.poseKeys) if (t >= pk[0]) key = typeof pk[1] === "string" ? pk[1] : "pose";
      boxes.push({ id: m.c.id, x: cx - h * 0.3, y: cy, w: h * 0.6, h, cls: key + (f < 0 ? " l" : " r") });
    });
    const r = S3.renderer(W, H); r.render(scene, camera);
    out.drawImage(r.domElement, 0, 0, W, H);
    const o = new T.Vector3(0, 1, 0).project(camera);
    return { boxes, anchor: [(o.x + 1) / 2 * W, (1 - o.y) / 2 * H] };
  };
})();

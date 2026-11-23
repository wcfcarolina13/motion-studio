// motion-studio sprite stacks: fake-3D pixel props drawn on a canvas, as a pure function of t.
// A stack is a strip of flat top-down layers, ground layer first (stack.py carve makes one from four drawings).
// Each layer is rotated about its true centre, squashed for the camera's pitch and lifted, and sampled nearest into a
// buffer of one art pixel per pixel; the buffer gets one outline and is blown up by a whole-number scale. Same inputs,
// same pixels, so any frame renders alone and a re-render matches.
//
//   const bin = Stack.load("props/bin.png", 22);                 // the strip and its layer count (stack.py's .json)
//   window.STUDIO.ready = Stack.ready();                         // render.py waits for the strips to decode
//   K.run(t => { ctx.clearRect(0, 0, K.W, K.H);
//                Stack.draw(ctx, bin, 960, 700, { turn: t * 0.8, scale: 6, grow: K.prog(t, 0, 1.2) }); });
//
// (x, y) is the screen point the stack stands on (the centre of its ground layer). Options:
//   turn     radians; 0 shows the front (the bottom of the TOP drawing) to the camera
//   pitch    0.75 (default) for the usual angled top-down camera; 1 looks straight down; under 0.6 roofs vanish
//   spacing  art px between layers (1); 2-3 pulls the layers apart (an exploded view)
//   scale    whole screen px per art px (4); never fractional, or pixels smear
//   outline  "#1f1f2a" (default) or null: one ring around the whole stack, never per layer (per-layer rings show seams)
//   shadow   null (default) or { color: "#..", dx: 2, dy: 2 }: the stack's footprint on the ground, in art px
//   grow     0..1 (default 1): builds the stack from the ground up, each layer rising into place over the 4 before it
//   alpha    0..1 (default 1)
// Motion is kept in screen pixels and rounded once, at the final blit, so a slow drift doesn't jump in art-pixel steps.
// Frames are cached per option set, so a stack that doesn't change costs one blit a frame.
// The method comes from danielwh2/sprite-stacking (MIT); this file is written fresh.
(function () {
  const S = {};
  window.Stack = S;
  const pending = [];
  const hexRGBA = (h, a = 255) => {
    const v = parseInt(h.replace("#", "").slice(0, 6), 16);
    return ((a << 24) | ((v & 0xff) << 16) | (v & 0xff00) | ((v >> 16) & 0xff)) >>> 0;   // little-endian ABGR
  };

  S.load = (src, count) => {
    const st = { src, count, w: 0, h: 0, layers: null, cache: new Map() };
    const img = new Image();
    img.src = src;
    pending.push(img.decode().then(() => {
      if (img.width % count) throw new Error(`stack ${src}: ${img.width} px wide is not ${count} whole layers`);
      st.w = img.width / count; st.h = img.height;
      const c = document.createElement("canvas");
      c.width = img.width; c.height = img.height;
      const g = c.getContext("2d");
      g.drawImage(img, 0, 0);
      const all = new Uint32Array(g.getImageData(0, 0, c.width, c.height).data.buffer);
      st.layers = [];
      for (let z = 0; z < count; z++) {
        const L = new Uint32Array(st.w * st.h);
        for (let y = 0; y < st.h; y++) for (let x = 0; x < st.w; x++) L[y * st.w + x] = all[y * img.width + z * st.w + x];
        st.layers.push(L);
      }
      const foot = new Uint8Array(st.w * st.h);           // every layer's pixels flattened: the shadow's shape
      for (const L of st.layers) for (let i = 0; i < L.length; i++) if (L[i] >>> 24) foot[i] = 1;
      st.foot = foot;
    }).catch(e => { (window.STUDIO.warnings = window.STUDIO.warnings || []).push(String(e.message || e)); throw e; }));
    return st;
  };

  S.ready = () => Promise.all(pending);

  // the buffer's size and where the ground centre sits in it, for one stack and pitch
  const frame = (st, pitch, spacing) => {
    const r = Math.ceil(Math.hypot(st.w, st.h) / 2) + 2, lift = Math.round((st.count - 1) * spacing);
    const bw = 2 * r + 3, bh = Math.ceil(2 * r * pitch) + lift + 4;
    return { bw, bh, gx: r + 1, gy: bh - Math.ceil(r * pitch) - 2 };
  };

  // where a stack lands on screen: { x, y, w, h }, for layout and for keeping text clear of it
  S.box = (st, x, y, o = {}) => {
    const sc = o.scale || 4, f = frame(st, o.pitch || 0.75, o.spacing == null ? 1 : o.spacing);
    return { x: Math.round(x - f.gx * sc), y: Math.round(y - f.gy * sc), w: f.bw * sc, h: f.bh * sc };
  };

  const render = (st, o) => {
    const { turn, pitch, spacing, grow, outline, shadow } = o;
    const { bw, bh, gx, gy } = frame(st, pitch, spacing);
    const buf = new Uint32Array(bw * bh);
    const ca = Math.cos(turn), sa = Math.sin(turn), cx = (st.w - 1) / 2, cy = (st.h - 1) / 2;
    const sample = (layer, lift, put) => {
      for (let v = 0; v < bh; v++) {
        const dy = (v - (gy - lift)) / pitch;
        for (let u = 0; u < bw; u++) {
          const dx = u - gx;
          const sx = Math.round(cx + dx * ca + dy * sa), sy = Math.round(cy - dx * sa + dy * ca);
          if (sx < 0 || sy < 0 || sx >= st.w || sy >= st.h) continue;
          put(v * bw + u, sy * st.w + sx, layer);
        }
      }
    };
    if (shadow) {
      const col = hexRGBA(shadow.color || "#1f1f2a", Math.round(255 * (shadow.opacity == null ? 0.25 : shadow.opacity)));
      const sdx = shadow.dx == null ? 2 : shadow.dx, sdy = shadow.dy == null ? 2 : shadow.dy;
      const ca2 = ca, sa2 = sa;
      for (let v = 0; v < bh; v++) {
        const dy = (v - gy - sdy) / pitch;
        for (let u = 0; u < bw; u++) {
          const dx = u - gx - sdx;
          const sx = Math.round(cx + dx * ca2 + dy * sa2), sy = Math.round(cy - dx * sa2 + dy * ca2);
          if (sx >= 0 && sy >= 0 && sx < st.w && sy < st.h && st.foot[sy * st.w + sx]) buf[v * bw + u] = col;
        }
      }
    }
    const band = 4, front = grow * (st.count + band);
    for (let z = 0; z < st.count; z++) {
      const p = Math.max(0, Math.min(1, (front - z) / band));
      if (p <= 0) break;
      const e = p * p * (3 - 2 * p);
      sample(st.layers[z], Math.round(z * spacing * e), (i, j, L) => { const c = L[j]; if (c >>> 24) buf[i] = c; });
    }
    if (outline) {
      const ink = hexRGBA(outline), solid = i => (buf[i] >>> 24) === 255;
      const ring = [];
      for (let v = 0; v < bh; v++) for (let u = 0; u < bw; u++) {
        const i = v * bw + u;
        if (solid(i)) continue;
        if ((u > 0 && solid(i - 1)) || (u < bw - 1 && solid(i + 1)) || (v > 0 && solid(i - bw)) || (v < bh - 1 && solid(i + bw))) ring.push(i);
      }
      for (const i of ring) buf[i] = ink;
    }
    const c = document.createElement("canvas");
    c.width = bw; c.height = bh;
    c.getContext("2d").putImageData(new ImageData(new Uint8ClampedArray(buf.buffer), bw, bh), 0, 0);
    return { c, gx, gy };
  };

  S.draw = (ctx, st, x, y, opts = {}) => {
    if (!st.layers) return;                                // not decoded yet: render.py waits on Stack.ready()
    const o = {
      turn: opts.turn || 0, pitch: opts.pitch || 0.75, spacing: opts.spacing == null ? 1 : opts.spacing,
      grow: opts.grow == null ? 1 : Math.max(0, Math.min(1, opts.grow)),
      outline: opts.outline === undefined ? "#1f1f2a" : opts.outline, shadow: opts.shadow || null,
    };
    const scale = opts.scale || 4;
    if (scale !== Math.round(scale)) throw new Error(`Stack.draw: scale ${scale} must be a whole number`);
    if (o.grow <= 0) return;
    const key = [o.turn.toFixed(6), o.pitch, o.spacing, o.grow.toFixed(5), o.outline, JSON.stringify(o.shadow)].join("|");
    let f = st.cache.get(key);
    if (!f) {
      f = render(st, o);
      st.cache.set(key, f);
      if (st.cache.size > 48) st.cache.delete(st.cache.keys().next().value);
    }
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    if (opts.alpha != null) ctx.globalAlpha *= opts.alpha;
    ctx.drawImage(f.c, Math.round(x - f.gx * scale), Math.round(y - f.gy * scale), f.c.width * scale, f.c.height * scale);
    ctx.restore();
  };
})();

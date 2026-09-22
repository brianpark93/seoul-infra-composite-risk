/* 시설물 복합위험도 지도 — 브라우저 해석엔진 + 지도 렌더러
 *
 * 해석엔진은 Python `src/model.py` 와 같은 식·같은 dt·같은 스텝수를 쓴다.
 *   Transfer_ji = G_ji(t) · K_ji · max(Rj − Ri, 0)        [diffusive]
 *                 G_ji(t) · K_ji · Rj · (1 − Ri)          [saturating]
 *   R_i(t+dt)   = clip(R_i + ΔR_direct_i + ΣTransfer·dt, 0, rmax)
 *   G_ji(t)     = 1 iff t ≥ act ∧ (diffusive면 Rj > Ri) ∧ Ri < rmax
 *
 * 직접위험도 R_direct 는 Python 에서 균일격자로 뽑아 보낸 값을 선형보간해 쓴다
 * (PCHIP 재구현으로 scipy 와 어긋나는 것을 피하려고).
 */
'use strict';

/* ---------- 위험도 램프 (matplotlib 렌더러와 동일) ---------- */
const RAMP = ['#1a9850','#52b151','#86c96a','#b7e07d','#d9ef8b','#ffffbf','#fee999','#fdc877','#fca55a','#f57547','#e34a33','#c22b26','#a50026'];
const RAMP_RGB = RAMP.map(h => [parseInt(h.slice(1,3),16), parseInt(h.slice(3,5),16),
                                parseInt(h.slice(5,7),16)]);
const BANDS = [[0,'관심'],[0.25,'주의'],[0.5,'경계'],[0.75,'심각']];

function rampRGB(v) {
  if (!Number.isFinite(v)) v = 0;
  const x = Math.max(0, Math.min(1, v)) * (RAMP_RGB.length - 1);
  const i = Math.min(Math.floor(x), RAMP_RGB.length - 2), f = x - i;
  const a = RAMP_RGB[i], b = RAMP_RGB[i+1];
  return [a[0]+(b[0]-a[0])*f, a[1]+(b[1]-a[1])*f, a[2]+(b[2]-a[2])*f];
}
const rampCSS = v => { const c = rampRGB(v).map(Math.round); return `rgb(${c[0]},${c[1]},${c[2]})`; };
/* 고정 임계 대신 대비를 계산해 고른다. 노랑이 가운데라 임계 하나로는 안 맞는다. */
function relLum(rgb) {
  const ch = c => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2]);
}
const inkOn = v => {
  const L = relLum(rampRGB(v));
  return (1.05 / (L + 0.05)) >= ((L + 0.05) / 0.0561) ? '#fff' : '#0b0b0b';
};

/* ---------- 지도 팔레트 ---------- */
const MAP = {
  paper:'#f4f1ec', bld:'#e4dfd5', bldEdge:'#d8d2c5', water:'#cadcec',
  rail:'#a9b3c0', tunnel:'#bdb3a2', casing:'#d9d3c7', fill:'#fdfcfa', label:'#8e8a80'
};
const ROADW = { motorway:[9,6], trunk:[8,5.2], primary:[7,4.4], secondary:[5.6,3.4],
                tertiary:[4.4,2.6], residential:[3,1.7], unclassified:[2.6,1.4] };
const ROAD_ORDER = ['unclassified','residential','tertiary','secondary','primary','trunk','motorway'];
const NAMED = new Set(['motorway','trunk','primary','secondary']);

const NSAVE = 601;

/* ---------- 상태 ---------- */
const S = {
  bundle:null, osm:{}, direct:{}, scn:null, tag:null,
  res:null, ti:0, playing:false, raf:0, lastT:0,
  interdep:true, form:'diffusive', K:[], sel:-1, hover:-1,
  view:null, base:null, field:null
};

const $ = id => document.getElementById(id);

/* ================= 해석엔진 ================= */
function directAt(d, i, t) {
  const x = (t - d.t0) / (d.t1 - d.t0) * (d.n - 1);
  const k = Math.max(0, Math.min(d.n - 2, Math.floor(x))), f = x - k;
  const v = d.v[i];
  return v[k] + (v[k+1] - v[k]) * f;
}

function solve(scn, d, opts) {
  const a = scn.analysis, n = scn.facilities.length, L = scn.links;
  const dt = a.dt, steps = Math.round((a.t1 - a.t0) / dt), rmax = a.rmax;
  const diffusive = opts.form === 'diffusive';

  let R = new Float64Array(n), prev = new Float64Array(n);
  for (let i = 0; i < n; i++) { R[i] = directAt(d, i, a.t0); prev[i] = R[i]; }

  const times = new Float64Array(NSAVE);
  const comp = [], dir = [];
  for (let i = 0; i < n; i++) { comp.push(new Float64Array(NSAVE)); dir.push(new Float64Array(NSAVE)); }
  const gOn = L.map(() => new Uint8Array(NSAVE));
  const rate = new Float64Array(n);

  let si = 0;
  const saveEvery = (a.t1 - a.t0) / (NSAVE - 1);
  const record = (t) => {
    times[si] = t;
    for (let i = 0; i < n; i++) { const dv = directAt(d, i, t); dir[i][si] = dv; comp[i][si] = R[i]; }
    for (let li = 0; li < L.length; li++) {
      const l = L[li], on = t >= l.act && R[l.t] < rmax && (!diffusive || R[l.s] > R[l.t]);
      gOn[li][si] = (opts.interdep && on) ? 1 : 0;
    }
    si++;
  };
  record(a.t0);

  for (let step = 1; step <= steps; step++) {
    const t = a.t0 + step * dt;
    rate.fill(0);
    if (opts.interdep) {
      for (let li = 0; li < L.length; li++) {
        const l = L[li];
        if (t < l.act) continue;
        const rs = R[l.s], rt = R[l.t];
        if (rt >= rmax) continue;
        if (diffusive) { if (rs <= rt) continue; rate[l.t] += opts.K[li] * (rs - rt); }
        else { rate[l.t] += opts.K[li] * rs * (1 - rt); }
      }
    }
    for (let i = 0; i < n; i++) {
      const cur = directAt(d, i, t);
      let v = R[i] + (cur - prev[i]) + rate[i] * dt;
      R[i] = v < 0 ? 0 : (v > rmax ? rmax : v);
      prev[i] = cur;
    }
    if (si < NSAVE && t + dt / 2 >= a.t0 + si * saveEvery) record(a.t0 + si * saveEvery);
  }
  while (si < NSAVE) record(a.t1);
  return { times, comp, dir, gOn };
}

/* ================= 좌표 변환 ================= */
function buildView(scn, w, h) {
  const xs = scn.facilities.map(f => f.xy[0]), ys = scn.facilities.map(f => f.xy[1]);
  const cx = (Math.max(...xs) + Math.min(...xs)) / 2, cy = (Math.max(...ys) + Math.min(...ys)) / 2;
  const half = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2 * 1.30 + 60;
  const halfX = half * (w / h) > half ? half * (w / h) : half;
  const scale = Math.min(w / (2 * halfX), h / (2 * half));
  return {
    w, h, scale,
    X: x => (x - cx) * scale + w / 2,
    Y: y => h / 2 - (y - cy) * scale,
    invX: px => (px - w / 2) / scale + cx,
    invY: py => (h / 2 - py) / scale + cy,
    cx, cy, half, halfX
  };
}

/* ================= 기저도 (오프스크린 캐시) ================= */
function drawBasemap(osm, view) {
  const c = document.createElement('canvas');
  c.width = view.w; c.height = view.h;
  const g = c.getContext('2d');
  g.fillStyle = MAP.paper; g.fillRect(0, 0, view.w, view.h);
  if (!osm) return c;
  const path = (pts) => { g.beginPath(); for (let i = 0; i < pts.length; i++) {
    const p = pts[i]; (i ? g.lineTo : g.moveTo).call(g, view.X(p[0]), view.Y(p[1])); } };

  g.lineJoin = g.lineCap = 'round';
  for (const w of osm.water) { path(w.p);
    if (w.closed) { g.closePath(); g.fillStyle = MAP.water; g.fill(); }
    else { g.strokeStyle = MAP.water; g.lineWidth = 3; g.stroke(); } }

  g.fillStyle = MAP.bld; g.strokeStyle = MAP.bldEdge; g.lineWidth = 0.4;
  for (const b of osm.buildings) { path(b); g.closePath(); g.fill(); g.stroke(); }

  const byCls = {};
  for (const r of osm.roads) (byCls[r.c] ||= []).push(r);
  for (const pass of [0, 1]) {
    g.strokeStyle = pass ? MAP.fill : MAP.casing;
    for (const cls of ROAD_ORDER) {
      const arr = byCls[cls]; if (!arr) continue;
      g.lineWidth = ROADW[cls][pass];
      g.beginPath();
      for (const r of arr) for (let i = 0; i < r.p.length; i++) {
        const p = r.p[i]; (i ? g.lineTo : g.moveTo).call(g, view.X(p[0]), view.Y(p[1])); }
      g.stroke();
    }
  }
  g.strokeStyle = MAP.tunnel; g.lineWidth = 4.2; g.setLineDash([5, 3.5]);
  g.beginPath();
  for (const r of osm.roads) if (r.t) for (let i = 0; i < r.p.length; i++) {
    const p = r.p[i]; (i ? g.lineTo : g.moveTo).call(g, view.X(p[0]), view.Y(p[1])); }
  g.stroke();

  g.strokeStyle = MAP.rail; g.lineWidth = 1.6; g.setLineDash([6, 4]);
  g.beginPath();
  for (const r of osm.rails) for (let i = 0; i < r.p.length; i++) {
    const p = r.p[i]; (i ? g.lineTo : g.moveTo).call(g, view.X(p[0]), view.Y(p[1])); }
  g.stroke(); g.setLineDash([]);

  // 주요 도로 이름
  g.font = '600 10.5px "Malgun Gothic", sans-serif';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  const seen = new Set(), m = 0.10;
  for (const r of osm.roads) {
    if (!r.n || seen.has(r.n) || !NAMED.has(r.c)) continue;
    const inside = r.p.filter(p => { const X = view.X(p[0]), Y = view.Y(p[1]);
      return X > view.w * m && X < view.w * (1 - m) && Y > view.h * m && Y < view.h * (1 - m); });
    if (!inside.length) continue;
    const p = inside[Math.floor(inside.length / 2)];
    const q = inside[Math.max(0, Math.floor(inside.length / 2) - 1)];
    let ang = Math.atan2(view.Y(p[1]) - view.Y(q[1]), view.X(p[0]) - view.X(q[0]));
    if (ang > Math.PI / 2) ang -= Math.PI; if (ang < -Math.PI / 2) ang += Math.PI;
    g.save(); g.translate(view.X(p[0]), view.Y(p[1])); g.rotate(ang);
    const wdt = g.measureText(r.n).width;
    g.fillStyle = 'rgba(244,241,236,0.78)'; g.fillRect(-wdt/2-3, -7, wdt+6, 14);
    g.fillStyle = MAP.label; g.fillText(r.n, 0, 0); g.restore();
    seen.add(r.n);
  }
  return c;
}

/* ================= 위험도 IDW 연속면 ================= */
function buildField(scn, view) {
  const GW = 150, GH = Math.max(8, Math.round(GW * view.h / view.w));
  const n = scn.facilities.length;
  const width = (view.w / view.scale);
  const r0 = Math.max(55, 0.055 * width), fade = Math.max(260, 0.34 * width);
  const w = new Float32Array(GW * GH * n), wsum = new Float32Array(GW * GH);
  const fd = new Float32Array(GW * GH);
  for (let gy = 0; gy < GH; gy++) for (let gx = 0; gx < GW; gx++) {
    const px = (gx + 0.5) / GW * view.w, py = (gy + 0.5) / GH * view.h;
    const X = view.invX(px), Y = view.invY(py);
    const o = gy * GW + gx; let s = 0, dmin = Infinity;
    for (let i = 0; i < n; i++) {
      const f = scn.facilities[i];
      const d2 = (X - f.xy[0]) ** 2 + (Y - f.xy[1]) ** 2;
      const ww = 1 / (d2 + r0 * r0);
      w[o * n + i] = ww; s += ww; if (d2 < dmin) dmin = d2;
    }
    wsum[o] = s;
    fd[o] = Math.max(0, 1 - (Math.sqrt(dmin) / fade) ** 2);
  }
  const img = new ImageData(GW, GH);
  return { GW, GH, n, w, wsum, fd, img };
}

function paintField(F, vals, g, view) {
  if (!F || vals.length !== F.n) return;   // 시나리오 전환 도중의 낡은 격자
  const d = F.img.data;
  for (let o = 0; o < F.GW * F.GH; o++) {
    let s = 0;
    for (let i = 0; i < F.n; i++) s += F.w[o * F.n + i] * vals[i];
    const v = s / F.wsum[o];
    const c = rampRGB(v), k = o * 4;
    d[k] = c[0]; d[k+1] = c[1]; d[k+2] = c[2];
    d[k+3] = Math.min(1, v / 0.85) * 0.62 * F.fd[o] * 255;
  }
  const tmp = document.createElement('canvas');
  tmp.width = F.GW; tmp.height = F.GH;
  tmp.getContext('2d').putImageData(F.img, 0, 0);
  g.imageSmoothingEnabled = true;
  g.drawImage(tmp, 0, 0, view.w, view.h);
}

/* ================= 렌더 ================= */
function render() {
  const scn = S.scn, view = S.view, cv = $('map'), g = cv.getContext('2d');
  if (!scn || !view) return;
  const i = S.ti, n = scn.facilities.length;
  const comp = [], dir = [];
  for (let k = 0; k < n; k++) { comp.push(S.res.comp[k][i]); dir.push(S.res.dir[k][i]); }
  const shown = S.interdep ? comp : dir;

  g.clearRect(0, 0, view.w, view.h);
  g.drawImage(S.base, 0, 0);
  paintField(S.field, shown, g, view);

  // 글로우
  for (let k = 0; k < n; k++) {
    const f = scn.facilities[k], v = shown[k];
    if (v < 0.02) continue;
    const X = view.X(f.xy[0]), Y = view.Y(f.xy[1]);
    const R = 16 + 52 * v, c = rampRGB(v).map(Math.round);
    const grd = g.createRadialGradient(X, Y, 0, X, Y, R);
    grd.addColorStop(0, `rgba(${c[0]},${c[1]},${c[2]},${0.34 * v})`);
    grd.addColorStop(1, `rgba(${c[0]},${c[1]},${c[2]},0)`);
    g.fillStyle = grd; g.beginPath(); g.arc(X, Y, R, 0, 7); g.fill();
  }

  // 링크
  const phase = (performance.now() / 55) % 18;
  scn.links.forEach((l, li) => {
    const a = scn.facilities[l.s], b = scn.facilities[l.t];
    const x1 = view.X(a.xy[0]), y1 = view.Y(a.xy[1]);
    const x2 = view.X(b.xy[0]), y2 = view.Y(b.xy[1]);
    const on = S.res.gOn[li][i];
    const rs = comp[l.s], rt = comp[l.t];
    const rate = on ? (S.form === 'diffusive' ? S.K[li] * Math.max(rs - rt, 0)
                                              : S.K[li] * rs * (1 - rt)) : 0;
    const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len, pad = 24;
    const ax = x1 + ux * pad, ay = y1 + uy * pad;
    const bx = x2 - ux * pad, by = y2 - uy * pad;
    g.save();
    if (on && rate > 1e-9) {
      g.strokeStyle = '#b32f24'; g.lineWidth = 1.6 + 4.2 * Math.min(rate / 0.6, 1);
      g.setLineDash([5, 4]); g.lineDashOffset = -phase; g.globalAlpha = 0.92;
    } else { g.strokeStyle = '#cdcbc4'; g.lineWidth = 1.2; g.globalAlpha = 0.6; }
    g.beginPath(); g.moveTo(ax, ay); g.lineTo(bx, by); g.stroke();
    g.setLineDash([]);
    const ah = on && rate > 1e-9 ? 9 : 6, a2 = Math.atan2(dy, dx);
    g.beginPath(); g.moveTo(bx, by);
    g.lineTo(bx - ah * Math.cos(a2 - 0.42), by - ah * Math.sin(a2 - 0.42));
    g.lineTo(bx - ah * Math.cos(a2 + 0.42), by - ah * Math.sin(a2 + 0.42));
    g.closePath(); g.fillStyle = g.strokeStyle; g.fill();
    g.restore();
  });

  // 노드
  g.textAlign = 'center'; g.textBaseline = 'middle';
  scn.facilities.forEach((f, k) => {
    const X = view.X(f.xy[0]), Y = view.Y(f.xy[1]);
    const ro = 22, ri = 15;
    g.beginPath(); g.arc(X, Y, ro, 0, 7);
    g.fillStyle = rampCSS(dir[k]); g.fill();
    g.lineWidth = (k === S.sel || k === S.hover) ? 2.6 : 1.4;
    g.strokeStyle = (k === S.sel || k === S.hover) ? '#0b0b0b' : '#3b3a37';
    if (f.depth < 0) g.setLineDash([3.2, 2.2]);
    g.stroke(); g.setLineDash([]);
    g.beginPath(); g.arc(X, Y, ri, 0, 7);
    g.fillStyle = rampCSS(shown[k]); g.fill();
    g.font = '700 11px "Malgun Gothic", sans-serif';
    g.fillStyle = inkOn(shown[k]); g.fillText(shown[k].toFixed(2), X, Y + 0.5);

    const lbl = f.short + (f.kind === 'medium' ? ' ◇' : f.kind === 'function' ? ' ▽' : '');
    const dep = f.depth < 0 ? `GL-${Math.abs(f.depth)} m` : (f.depth > 0 ? `GL+${f.depth} m` : '');
    g.font = '700 11px "Malgun Gothic", sans-serif';
    const wd = Math.max(g.measureText(lbl).width, dep ? g.measureText(dep).width : 0);
    const ty = Y - ro - (dep ? 24 : 13);
    g.fillStyle = 'rgba(252,252,251,0.88)';
    g.fillRect(X - wd/2 - 4, ty - 8, wd + 8, dep ? 27 : 16);
    g.fillStyle = '#0b0b0b'; g.fillText(lbl, X, ty);
    if (dep) g.fillText(dep, X, ty + 13);
  });

  drawScale(g, view);
  drawNorth(g, view);
}

function drawScale(g, view) {
  const widthM = view.w / view.scale;
  let bar = 100;
  for (const c of [50, 100, 200, 250, 500, 1000]) if (c <= widthM * 0.26) bar = c;
  const px = bar * view.scale, x0 = 24, y0 = view.h - 26;
  g.strokeStyle = '#0b0b0b'; g.lineWidth = 2.4; g.beginPath();
  g.moveTo(x0, y0); g.lineTo(x0 + px, y0);
  g.moveTo(x0, y0 - 5); g.lineTo(x0, y0); g.moveTo(x0 + px, y0 - 5); g.lineTo(x0 + px, y0);
  g.stroke();
  g.font = '700 11px "Malgun Gothic", sans-serif'; g.fillStyle = '#0b0b0b';
  g.textAlign = 'center'; g.fillText(`${bar} m`, x0 + px / 2, y0 - 12);
}

function drawNorth(g, view) {
  const x = view.w - 30, y = 34;
  g.strokeStyle = '#0b0b0b'; g.lineWidth = 2; g.beginPath();
  g.moveTo(x, y + 28); g.lineTo(x, y + 6); g.stroke();
  g.beginPath(); g.moveTo(x, y); g.lineTo(x - 5, y + 10); g.lineTo(x + 5, y + 10);
  g.closePath(); g.fillStyle = '#0b0b0b'; g.fill();
  g.font = '700 12px "Malgun Gothic", sans-serif'; g.textAlign = 'center';
  g.fillText('N', x, y - 8);
}

/* ================= 패널 ================= */
function clockText(scn, t) {
  if (scn.clock0 == null) return `t = ${t.toFixed(scn.analysis.t1 <= 3 ? 2 : 1)} h`;
  const tot = Math.round((scn.clock0 + t) * 60), d = tot >= 1440 ? '8/9 ' : '';
  const m = tot % 1440;
  return `${d}${String(Math.floor(m/60)).padStart(2,'0')}:${String(m%60).padStart(2,'0')}`;
}

function updatePanels() {
  const scn = S.scn, i = S.ti, t = S.res.times[i];
  $('clock').textContent = clockText(scn, t);
  $('tlabel').textContent = `${t.toFixed(scn.analysis.t1 <= 3 ? 2 : 1)} h`;
  const past = scn.events.filter(e => e.t <= t + 1e-9);
  const ev = past.length ? past[past.length-1] : null;
  $('eventLabel').textContent = ev ? ev.label : '재난 발생 전';
  $('eventLabel').style.color = ev ? ev.color : 'var(--ink2)';

  const rows = scn.facilities.map((f, k) => ({
    f, k, c: S.interdep ? S.res.comp[k][i] : S.res.dir[k][i], d: S.res.dir[k][i]
  })).sort((a, b) => b.c - a.c);

  $('ranks').innerHTML = rows.map(r => `
    <div class="rank">
      <div class="nm"><b>${esc(r.f.short)}</b><span>${r.c.toFixed(3)}
        <small style="color:var(--muted)">(직접 ${r.d.toFixed(3)})</small></span></div>
      <div class="bar"><i style="width:${(r.c*100).toFixed(1)}%;background:${rampCSS(r.c)}"></i>
        <u style="width:${(r.d*100).toFixed(1)}%"></u></div>
    </div>`).join('');

  if (S.sel >= 0) renderDetail(S.sel);
}

function renderLegend() {
  $('legend').innerHTML = BANDS.map((b, i) => {
    const hi = i+1 < BANDS.length ? BANDS[i+1][0] : 1, mid = (b[0]+hi)/2;
    return `<div style="background:${rampCSS(mid)};color:${inkOn(mid)}">${b[1]}</div>`;
  }).join('');
}

function renderTicks() {
  const scn = S.scn, a = scn.analysis, el = $('ticks');
  const N = 6;
  el.innerHTML = Array.from({length: N+1}, (_, i) => {
    const t = a.t0 + (a.t1-a.t0)*i/N;
    return `<span style="left:${(i/N*100).toFixed(2)}%">${clockText(scn, t)}</span>`;
  }).join('');
}

function renderKList() {
  const scn = S.scn;
  $('klist').innerHTML = scn.links.map((l, i) => {
    const on = S.res.gOn[i].some(v => v);
    return `<div class="k${on ? '' : ' off'}" data-i="${i}">
      <div class="top2"><b>${esc(scn.facilities[l.s].short)} → ${esc(scn.facilities[l.t].short)}</b>
        <span id="kv${i}">${S.K[i].toFixed(3)}</span></div>
      <p class="mech">${esc(l.mech)}${on ? '' : ' · <em>현재 설정에서 미발현</em>'}</p>
      <input type="range" min="0" max="1.2" step="0.005" value="${S.K[i]}" data-k="${i}">
    </div>`;
  }).join('');
  $('klist').querySelectorAll('input[data-k]').forEach(inp => {
    inp.addEventListener('input', () => {
      const i = +inp.dataset.k; S.K[i] = +inp.value;
      $('kv'+i).textContent = S.K[i].toFixed(3);
      recompute(false);
    });
  });
}

function renderDetail(k) {
  const f = S.scn.facilities[k], i = S.ti;
  const c = S.res.comp[k][i], d = S.res.dir[k][i];
  const inflow = S.scn.links.filter(l => l.t === k);
  $('detail').innerHTML = `
    <div class="num">
      <div><small>직접위험도</small><b>${d.toFixed(3)}</b></div>
      <div><small>복합위험도</small><b style="color:${rampCSS(c)}">${c.toFixed(3)}</b></div>
      <div><small>상호의존성지수</small><b>${Math.max(c-d,0).toFixed(3)}</b></div>
    </div>
    <dl>
      <dt>${esc(f.name)}</dt>
      <dd>${f.depth < 0 ? `지하 ${Math.abs(f.depth)} m · ` : ''}${f.exposed ? '재난에 직접 노출' : '전이로만 위험 증가'}</dd>
      ${f.E != null ? `<dt>E · V</dt><dd>노출성 ${f.E} × 취약성 ${f.V}</dd>` : ''}
      ${f.note ? `<dt>근거</dt><dd>${esc(f.note)}</dd>` : ''}
      ${f.ev ? `<dt>E·V 설정 근거</dt><dd>${esc(f.ev)}</dd>` : ''}
      ${inflow.length ? `<dt>유입 링크 ${inflow.length}개</dt><dd>${
        inflow.map(l => `${esc(S.scn.facilities[l.s].short)} (K=${S.K[S.scn.links.indexOf(l)].toFixed(3)}, t≥${l.act}h)`).join('<br>')}</dd>` : ''}
    </dl>`;
}

const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, m =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));

/* ================= 흐름 제어 ================= */
function recompute(resetTime) {
  S.res = solve(S.scn, S.direct[S.tag], { K: S.K, form: S.form, interdep: S.interdep });
  if (resetTime) { S.ti = 0; $('time').value = 0; }
  renderKList();
  updatePanels();
  // 시나리오를 바꾼 직후에는 field/base 가 아직 이전 시나리오 것이다.
  // 그대로 그리면 시설물 수가 달라 값 배열을 벗어나 예외가 난다 → resize() 가 다시 그린다.
  if (S.field && S.field.n === S.scn.facilities.length) render();
}

function resize() {
  const cv = $('map'), wrap = cv.parentElement;
  const w = wrap.clientWidth, h = Math.max(380, Math.round(w * 0.60));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  cv.width = w * dpr; cv.height = h * dpr; cv.style.height = h + 'px';
  cv.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
  S.view = buildView(S.scn, w, h);
  S.base = drawBasemap(S.osm[S.scn.site.osm], S.view);
  S.field = buildField(S.scn, S.view);
  render();
}

async function loadScenario(tag, H) {
  S.tag = tag;
  S.scn = S.bundle.scenarios.find(s => s.tag === tag);
  document.querySelectorAll('#scnTabs button').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.tag === tag)));
  $('scnTitle').textContent = `${S.scn.id} · ${S.scn.title}`;
  $('scnSite').textContent = `${S.scn.site.name}${S.scn.site.origin ? ' · ' + S.scn.site.origin : ''}`;
  S.form = (H && H.form) || S.scn.analysis.form;
  $('form').value = S.form;
  S.K = S.scn.links.map(l => l.K);
  S.sel = -1;
  $('detail').innerHTML = '';

  const key = S.scn.site.osm;
  if (key && !S.osm[key]) S.osm[key] = await (await fetch(`data/osm_${key}.json`)).json();
  if (!S.direct[tag]) S.direct[tag] = await (await fetch(`data/direct_${tag}.json`)).json();

  renderTicks();
  recompute(true);
  if (H && H.ti) {
    S.ti = Math.max(0, Math.min(NSAVE - 1, H.ti));
    $('time').value = Math.round(S.ti / (NSAVE - 1) * 600);
    updatePanels();
  }
  resize();
}

function step(now) {
  if (!S.playing) return;
  if (!S.lastT) S.lastT = now;
  const dt = now - S.lastT;
  if (dt > 55) {
    S.lastT = now;
    S.ti = (S.ti + 2) % NSAVE;
    $('time').value = Math.round(S.ti / (NSAVE-1) * 600);
    updatePanels();
  }
  render();
  S.raf = requestAnimationFrame(step);
}

function setPlaying(on) {
  S.playing = on; $('play').textContent = on ? '❚❚' : '▶';
  S.lastT = 0;
  if (on) S.raf = requestAnimationFrame(step); else cancelAnimationFrame(S.raf);
}

/* ---------- URL 해시 (발표용 딥링크) ----------
 * #G/360/1/diffusive  =  시나리오 G, 시간 인덱스 360, 상호의존 ON, diffusive
 */
function readHash() {
  const h = decodeURIComponent(location.hash.replace(/^#/, ''));
  if (!h) return null;
  const [tag, ti, inter, form] = h.split('/');
  return { tag, ti: ti ? +ti : 0, interdep: inter !== '0',
           form: form || null };
}
let hashTimer = 0;
function writeHash() {
  clearTimeout(hashTimer);
  hashTimer = setTimeout(() => {
    const h = `${S.tag}/${S.ti}/${S.interdep ? 1 : 0}/${S.form}`;
    history.replaceState(null, '', '#' + h);
  }, 250);
}

/* ================= 초기화 ================= */
(async function init() {
  S.bundle = await (await fetch('data/scenarios.json')).json();
  $('scnTabs').innerHTML = S.bundle.order.map(tag => {
    const s = S.bundle.scenarios.find(x => x.tag === tag);
    const real = tag === 'G' ? '<span class="real">● 실데이터</span> ' : '';
    return `<button data-tag="${tag}" aria-pressed="false">${real}${esc(s.id.replace('Scenario ','시나리오 '))}</button>`;
  }).join('');
  $('scnTabs').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (b) {
      setPlaying(false);
      loadScenario(b.dataset.tag).then(writeHash).catch(err => {
        console.error('시나리오 전환 실패', err);
        $('eventLabel').textContent = '시나리오를 불러오지 못했습니다';
        $('eventLabel').style.color = '#a50026';
      });
    }
  });

  $('time').addEventListener('input', () => {
    S.ti = Math.round(+$('time').value / 600 * (NSAVE-1));
    updatePanels(); render(); writeHash();
  });
  $('play').addEventListener('click', () => setPlaying(!S.playing));
  $('interdep').addEventListener('change', () => {
    S.interdep = $('interdep').checked; recompute(false); writeHash();
  });
  $('form').addEventListener('change', () => {
    S.form = $('form').value;
    $('formHint').textContent = S.form === 'diffusive'
      ? '위험이 낮은 쪽으로만 전달됩니다. 되먹임 링크는 발현하지 않습니다.'
      : '되먹임과 증폭을 허용합니다. 긴 해석창에서는 포화할 수 있습니다.';
    recompute(false); writeHash();
  });
  $('kreset').addEventListener('click', () => {
    S.K = S.scn.links.map(l => l.K); recompute(false);
  });

  const cv = $('map');
  const pick = (e) => {
    const r = cv.getBoundingClientRect();
    const px = e.clientX - r.left, py = e.clientY - r.top;
    let best = -1, bd = 26 * 26;
    S.scn.facilities.forEach((f, k) => {
      const d = (S.view.X(f.xy[0]) - px) ** 2 + (S.view.Y(f.xy[1]) - py) ** 2;
      if (d < bd) { bd = d; best = k; }
    });
    return { k: best, px, py };
  };
  cv.addEventListener('mousemove', e => {
    const { k, px, py } = pick(e);
    S.hover = k; cv.style.cursor = k >= 0 ? 'pointer' : 'default';
    const tip = $('tip');
    if (k >= 0) {
      const f = S.scn.facilities[k], i = S.ti;
      tip.innerHTML = `<b>${esc(f.name)}</b>복합 ${S.res.comp[k][i].toFixed(3)} · 직접 ${S.res.dir[k][i].toFixed(3)}`;
      tip.hidden = false;
      tip.style.left = Math.min(px + 14, S.view.w - 260) + 'px';
      tip.style.top = (py + 14) + 'px';
    } else tip.hidden = true;
    render();
  });
  cv.addEventListener('mouseleave', () => { S.hover = -1; $('tip').hidden = true; render(); });
  cv.addEventListener('click', e => { const { k } = pick(e); S.sel = k; if (k >= 0) renderDetail(k); render(); });

  window.addEventListener('resize', () => { if (S.scn) resize(); });
  renderLegend();
  $('formHint').textContent = '위험이 낮은 쪽으로만 전달됩니다. 되먹임 링크는 발현하지 않습니다.';

  const H = readHash();
  const start = (H && S.bundle.scenarios.some(s => s.tag === H.tag)) ? H.tag : S.bundle.order[0];
  if (H) {
    S.interdep = H.interdep; $('interdep').checked = H.interdep;
  }
  await loadScenario(start, H);
})();

"""실제 OSM 기저도 렌더링 + 위험도 연속면(heat field).

기저도는 `fetch_osm_basemap.py` 가 받아 `data/osm/<key>.json` 에 캐시한
OpenStreetMap 지오메트리(© OpenStreetMap contributors, ODbL)를 쓴다.
좌표는 테스트베드 기준점 기준 로컬 미터.

위험도 연속면
    시설물은 점이지만 위험은 면으로 읽어야 한다. 노드 위험도를 역거리가중(IDW)으로
    보간해 반투명 적색 면으로 깐다.

        w_i(p) = 1 / (d_i(p)^2 + r0^2)
        R(p)   = Σ w_i R_i / Σ w_i
        α(p)   = clip(R(p)/α_ref, 0, 1) · α_max · fade(최근접거리)

    r0 는 영향 반경 척도, fade 는 노드에서 멀어질수록 면을 흐리게 해
    "데이터가 없는 곳까지 색칠하는" 과잉해석을 막는다.
    **이 면은 침수심 분포가 아니라 노드 위험도의 공간 보간이다.**
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from matplotlib.collections import LineCollection, PolyCollection

DATA = Path(__file__).resolve().parent.parent / "data" / "osm"

# --- 지도 팔레트 (종이지도 톤) ---------------------------------------------
PAPER = "#f4f1ec"
BUILDING = "#e4dfd5"
BUILDING_EDGE = "#d8d2c5"
WATER = "#cadcec"
WATER_EDGE = "#b3c9dd"
RAIL = "#a9b3c0"
TUNNEL = "#bdb3a2"
ROAD_CASING = "#d9d3c7"
ROAD_FILL = "#fdfcfa"
LABEL = "#8e8a80"

# 도로 등급별 (casing 폭, fill 폭)  단위: 포인트
ROAD_W = {
    "motorway": (9.0, 6.0), "trunk": (8.0, 5.2), "primary": (7.0, 4.4),
    "secondary": (5.6, 3.4), "tertiary": (4.4, 2.6),
    "residential": (3.0, 1.7), "unclassified": (2.6, 1.4),
}
NAMED = ("motorway", "trunk", "primary", "secondary")


def load(site_key: str | None):
    if not site_key:
        return None
    p = DATA / f"{site_key}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def draw(ax, osm, *, compact=False, lang="ko", label_roads=True):
    """OSM 기저도를 그린다. 축 범위는 호출 전에 정해져 있어야 한다."""
    if osm is None:
        return False

    # 1) 수계
    polys = [np.asarray(w["pts"]) for w in osm.get("water", []) if w.get("closed")]
    if polys:
        ax.add_collection(PolyCollection(polys, facecolor=WATER,
                                         edgecolor=WATER_EDGE, linewidths=0.6,
                                         zorder=0.5))
    lines = [np.asarray(w["pts"]) for w in osm.get("water", []) if not w.get("closed")]
    if lines:
        ax.add_collection(LineCollection(lines, colors=WATER, linewidths=3.0,
                                         zorder=0.6, capstyle="round"))

    # 2) 건물
    b = [np.asarray(p) for p in osm.get("buildings", [])]
    if b:
        ax.add_collection(PolyCollection(b, facecolor=BUILDING,
                                         edgecolor=BUILDING_EDGE,
                                         linewidths=0.35, zorder=0.8))

    # 3) 도로 — casing 먼저 전부, 그 위에 fill 전부 (교차부가 깔끔해진다)
    by_cls = {}
    for r in osm.get("roads", []):
        by_cls.setdefault(r["cls"], []).append(np.asarray(r["pts"]))
    order = ["unclassified", "residential", "tertiary", "secondary",
             "primary", "trunk", "motorway"]
    for pass_idx, (color, wkey) in enumerate(((ROAD_CASING, 0), (ROAD_FILL, 1))):
        for cls in order:
            segs = by_cls.get(cls)
            if not segs:
                continue
            ax.add_collection(LineCollection(
                segs, colors=color, linewidths=ROAD_W[cls][wkey],
                zorder=1.0 + pass_idx * 0.3 + order.index(cls) * 0.01,
                capstyle="round", joinstyle="round"))

    # 3b) 터널 구간 — 지하를 지나는 도로는 파선 윤곽으로 따로 보여준다
    tun = [np.asarray(r["pts"]) for r in osm.get("roads", []) if r.get("tunnel")]
    if tun:
        ax.add_collection(LineCollection(tun, colors=TUNNEL, linewidths=4.2,
                                         linestyles=(0, (5, 3.5)), zorder=1.66,
                                         capstyle="round"))

    # 4) 철도
    rl = [np.asarray(r["pts"]) for r in osm.get("rails", [])]
    if rl:
        ax.add_collection(LineCollection(rl, colors=RAIL, linewidths=1.6,
                                         linestyles=(0, (6, 4)), zorder=1.7))

    # 5) 주요 도로 이름
    if label_roads and not compact:
        _label_roads(ax, osm, lang)
    return True


def _label_roads(ax, osm, lang):
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    mx, my = (x1 - x0) * 0.10, (y1 - y0) * 0.10
    seen = set()
    for r in osm.get("roads", []):
        name = r.get("name") or ""
        if not name or name in seen or r["cls"] not in NAMED:
            continue
        pts = np.asarray(r["pts"])
        inside = ((pts[:, 0] > x0 + mx) & (pts[:, 0] < x1 - mx)
                  & (pts[:, 1] > y0 + my) & (pts[:, 1] < y1 - my))
        if not inside.any():
            continue
        cand = pts[inside]
        p = cand[len(cand) // 2]
        # 선 방향으로 글자를 눕힌다
        k = min(max(len(cand) // 2, 1), len(cand) - 1)
        d = cand[k] - cand[k - 1]
        ang = float(np.degrees(np.arctan2(d[1], d[0])))
        if ang > 90:
            ang -= 180
        if ang < -90:
            ang += 180
        ax.text(p[0], p[1], name, fontsize=7.6, color=LABEL, rotation=ang,
                rotation_mode="anchor", ha="center", va="center", zorder=1.9,
                bbox=dict(boxstyle="round,pad=0.12", fc=PAPER, ec="none",
                          alpha=0.72))
        seen.add(name)


# --------------------------------------------------------------------------
# 위험도 연속면
# --------------------------------------------------------------------------
class RiskField:
    """노드 위험도의 IDW 보간면. 프레임마다 update() 로 값만 갈아끼운다."""

    def __init__(self, ax, node_xy, cmap, *, res=190, r0=None,
                 alpha_max=0.62, alpha_ref=0.85, fade_m=None):
        self.ax = ax
        self.cmap = cmap
        self.alpha_max = float(alpha_max)
        self.alpha_ref = float(alpha_ref)

        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        # 장면 폭에 비례시킨다. 고정 미터값을 쓰면 넓은 테스트베드(남산 등)에서
        # 면이 노드 주변에만 찍혀 사실상 사라진다.
        width = max(x1 - x0, 1.0)
        self.r0 = float(r0) if r0 else max(55.0, 0.055 * width)
        fade_m = float(fade_m) if fade_m else max(260.0, 0.34 * width)
        nx = res
        ny = max(int(res * (y1 - y0) / max(x1 - x0, 1e-6)), 8)
        gx = np.linspace(x0, x1, nx)
        gy = np.linspace(y0, y1, ny)
        GX, GY = np.meshgrid(gx, gy)

        P = np.asarray(node_xy, dtype=float)                  # (n,2)
        d2 = ((GX[..., None] - P[None, None, :, 0]) ** 2
              + (GY[..., None] - P[None, None, :, 1]) ** 2)   # (ny,nx,n)
        self.w = 1.0 / (d2 + self.r0 ** 2)
        self.wsum = self.w.sum(axis=2)
        dmin = np.sqrt(d2.min(axis=2))
        self.fade = np.clip(1.0 - (dmin / fade_m) ** 2, 0.0, 1.0)

        self.rgba = np.zeros((ny, nx, 4), dtype=float)
        self.im = ax.imshow(self.rgba, extent=(x0, x1, y0, y1), origin="lower",
                            interpolation="bilinear", zorder=2.0)

    def update(self, values):
        v = np.asarray(values, dtype=float)
        field = (self.w * v[None, None, :]).sum(axis=2) / self.wsum
        self.rgba[...] = self.cmap(np.clip(field, 0, 1))
        self.rgba[..., 3] = (np.clip(field / self.alpha_ref, 0, 1)
                             * self.alpha_max * self.fade)
        self.im.set_data(self.rgba)
        return self.im


# --------------------------------------------------------------------------
# 노드 글로우
# --------------------------------------------------------------------------
def make_glow(ax, xs, ys, n_layer=4, base=5200):
    """위험도가 높을수록 넓게 번지는 후광. 값은 update_glow 로 갱신."""
    layers = []
    for k in range(n_layer):
        s = base * (1.0 + 0.85 * k)
        sc = ax.scatter(xs, ys, s=s, c=["#ffffff"] * len(xs),
                        edgecolors="none", zorder=3.0 + 0.01 * k)
        sc.set_alpha(None)
        layers.append(sc)
    return layers


def update_glow(layers, values, cmap, strength=1.0):
    v = np.clip(np.asarray(values, dtype=float), 0, 1)
    for k, sc in enumerate(layers):
        rgba = np.array(cmap(v))
        rgba[:, 3] = (0.24 / (k + 1)) * (v ** 1.6) * strength
        sc.set_facecolor(rgba)

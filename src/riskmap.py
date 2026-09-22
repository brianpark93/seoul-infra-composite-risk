"""2D 공간 위험도 지도 — 시설물 배치 위에서 위험이 전파되는 과정을 시각화.

8월 개념문서(`시설물_기본위험도_복합위험도_큰그림_정리_KAIST.docx`) 6·9·10절이
최종 산출물로 제시한 **"시설물 위험도 지도 + 시설 간 의존·전이 네트워크"** 를 구현한다.

좌표계 — 평면도(plan view)
    x = 동서 방향 [m],  y = 남북 방향 [m]  (테스트베드 기준점 상대좌표, 축척 1:1)
    표고축은 쓰지 않는다. 시설물의 지하 심도는 노드 테두리(점선=지하)와
    "GL-18m" 주기로 표시한다.
    시설물 위치는 공개 지점을 기준으로 한 개념 배치이며 실제 대장좌표가 아니다.

색 설계 (dataviz 기준)
    위험도는 연속 크기값이므로 **단일 색상 순차 램프**(적색, 밝음→어두움)를 쓴다.
    통상적인 녹색→적색 램프는 적록색맹에서 안전/심각이 구분되지 않는다
    (검증기 실측 deutan ΔE 4.1 → FAIL). 본 램프는 OKLab 명도가 단조 감소하므로
    색각 이상에서도 명도만으로 순서가 읽힌다.
    색에만 의존하지 않도록 **모든 노드에 수치를 직접 표기**하고 우측에 순위 막대를 둔다.

노드 표현
    바깥 고리 = 직접위험도 R_direct   (상호의존을 고려하지 않았을 때의 값)
    안쪽 원   = 복합위험도 R_composite (상호의존 반영)
    두 색이 벌어진 정도가 곧 상호의존성지수 I 다.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import FancyArrowPatch

from . import basemap
from .model import Result, Scenario

# --- 순차 램프 (적색 단일 색상, OKLab 명도 단조 감소 → 색각 안전) -------------
RISK_RAMP = [
    "#fde5e0", "#fbd2ca", "#f9bcb1", "#f5a393", "#ef8975", "#e66f5a",
    "#d95441", "#c83f30", "#b32f24", "#9b241c", "#811c16", "#671611", "#4d100c",
]
RISK_CMAP = LinearSegmentedColormap.from_list("risk", RISK_RAMP)
NORM = Normalize(0.0, 1.0)

# 램프 6단계(#e66f5a)부터 흰 글씨가 3:1 을 넘는다.
_WHITE_TEXT_FROM = 0.42

INK = "#0b0b0b"
SECOND = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"
SOIL = "#f2efe9"
SOIL_LINE = "#c3c2b7"

# 위험도 구간 경계 (8월 문서 9절의 녹색→노랑→주황→빨강 4단계에 대응)
BANDS = [(0.00, "관심", "Low"), (0.25, "주의", "Guarded"),
         (0.50, "경계", "Elevated"), (0.75, "심각", "Severe")]


def use_korean_font():
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False


def risk_color(v: float):
    return RISK_CMAP(NORM(float(np.clip(v, 0, 1))))


def _text_on(v: float) -> str:
    return "#ffffff" if v >= _WHITE_TEXT_FROM else INK


def _short(f, lang):
    return f.short_ko if lang == "ko" else f.short_en


def _at(times, arr, t):
    """저장 시계열에서 시각 t 의 값을 선형보간."""
    return float(np.interp(t, times, arr))


def _transfer_rate(scenario: Scenario, link, comp, t, times):
    """시각 t 에서 링크의 순간 전달률."""
    if t < link.activation_time_hour:
        return 0.0
    rs = _at(times, comp[link.source_idx], t)
    rt = _at(times, comp[link.target_idx], t)
    if rt >= scenario.risk_max:
        return 0.0
    if scenario.transfer_form == "saturating":
        return link.K * rs * (1.0 - rt)
    return link.K * max(rs - rt, 0.0)


# --------------------------------------------------------------------------
# 지도 축 구성
# --------------------------------------------------------------------------
def _label_spot_on(ax, pts, scenario):
    """폴리라인 위에서 '화면 안이면서 시설물 노드에서 가장 먼' 지점을 고른다."""
    seg = []
    for a, b in zip(pts[:-1], pts[1:]):
        for s in np.linspace(0, 1, 40, endpoint=False):
            seg.append(a + (b - a) * s)
    seg.append(pts[-1])
    seg = np.asarray(seg)

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    mx, my = (x1 - x0) * 0.07, (y1 - y0) * 0.07
    inside = ((seg[:, 0] > x0 + mx) & (seg[:, 0] < x1 - mx)
              & (seg[:, 1] > y0 + my) & (seg[:, 1] < y1 - my))
    cand = seg[inside] if inside.any() else seg
    nodes = np.array([f.map_xy for f in scenario.facilities], dtype=float)
    d = np.min(np.hypot(cand[:, None, 0] - nodes[None, :, 0],
                        cand[:, None, 1] - nodes[None, :, 1]), axis=1)
    return cand[int(np.argmax(d))]


def _draw_basemap(ax, scenario: Scenario, lang: str, *, compact=False,
                  center=(0.0, 0.0)):
    """테스트베드 기저도: 주요 도로/철도/구역을 옅게 깔아 위치감을 준다."""
    style = {
        "road":   dict(color="#dedbd3", zorder=1),
        "bridge": dict(color="#cfcbc1", zorder=1),
        "rail":   dict(color="#c8d4e4", zorder=1),
        "tunnel": dict(color="#d6cfc4", zorder=1),
        "slope":  dict(color="#d7e0d1", zorder=1),
    }
    for seg in scenario.site.get("basemap", []):
        pts = np.asarray(seg["pts"], dtype=float)
        kind = seg.get("kind", "road")
        if kind == "zone":
            ax.fill(pts[:, 0], pts[:, 1], facecolor="#e8e2d6",
                    edgecolor="#c3bcab", linewidth=1.0, zorder=1, alpha=0.9)
            label_at = pts.mean(axis=0)
        else:
            st = style.get(kind, style["road"])
            lw = seg.get("width", 5.0)
            dash = (0, (7, 5)) if kind in ("tunnel", "rail") else "solid"
            ax.plot(pts[:, 0], pts[:, 1], linewidth=lw, solid_capstyle="round",
                    linestyle=dash, **st)
            # 도로 이름은 화면 안, 그리고 시설물 노드에서 가장 먼 빈 구간에 건다.
            label_at = _label_spot_on(ax, pts, scenario)
        if not compact:
            ax.annotate(seg["name_ko"] if lang == "ko" else seg["name_en"],
                        label_at, fontsize=8.0, color="#9a978e",
                        ha="center", va="center", zorder=2,
                        bbox=dict(boxstyle="round,pad=0.12", fc=SURFACE,
                                  ec="none", alpha=0.6))


def _scale_bar(ax, x0, x1, y0, lang):
    """축척 막대. 평면도이므로 거리 감각이 반드시 필요하다."""
    span = x1 - x0
    for cand in (50, 100, 200, 250, 500, 1000):
        if cand <= span * 0.26:
            bar = cand
    bx = x0 + span * 0.035
    by = y0 + (ax.get_ylim()[1] - y0) * 0.045
    ax.plot([bx, bx + bar], [by, by], color=INK, linewidth=2.6,
            solid_capstyle="butt", zorder=9)
    for xx in (bx, bx + bar):
        ax.plot([xx, xx], [by, by + span * 0.009], color=INK, linewidth=2.0, zorder=9)
    ax.text(bx + bar / 2, by + span * 0.014, f"{bar} m", ha="center", va="bottom",
            fontsize=8.4, color=INK, fontweight="bold", zorder=9)


def _north_arrow(ax, x1, y1, span, lang):
    ax.annotate("", xy=(x1 - span * 0.045, y1 - span * 0.035),
                xytext=(x1 - span * 0.045, y1 - span * 0.105),
                arrowprops=dict(arrowstyle="-|>", color=INK, linewidth=1.8),
                zorder=9)
    ax.text(x1 - span * 0.045, y1 - span * 0.028, "N", ha="center", va="bottom",
            fontsize=10.5, fontweight="bold", color=INK, zorder=9)


def _setup_map_axes(ax, scenario: Scenario, lang: str, *, compact=False):
    """평면도 축. 축척 1:1(equal aspect) 을 반드시 지킨다.

    화면 범위는 **시설물 분포**에 맞춘다. 기저도까지 포함해 잡으면 도로가
    화면을 지배하고 시설물이 한 덩어리로 뭉친다. 기저도는 축 밖으로 잘린다.

    기저도는 실제 OpenStreetMap 지오메트리를 쓴다(`data/osm/`). 캐시가 없으면
    시나리오 JSON 에 손으로 적어둔 약식 기저도로 되돌아간다.
    """
    xs = [f.map_xy[0] for f in scenario.facilities]
    ys = [f.map_xy[1] for f in scenario.facilities]
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 * 1.30 + 60

    ax.set_xlim(cx - half * 1.34, cx + half * 1.34)   # 가로가 넓은 화면에 맞춘다
    ax.set_ylim(cy - half, cy + half)
    ax.set_aspect("equal", adjustable="box")
    ax.set_facecolor(basemap.PAPER)

    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])

    osm = basemap.load(scenario.site.get("osm_key"))
    if not basemap.draw(ax, osm, compact=compact, lang=lang):
        _draw_basemap(ax, scenario, lang, compact=compact, center=(cx, cy))

    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    if not compact:
        _scale_bar(ax, x0, x1, y0, lang)
        _north_arrow(ax, x1, y1, x1 - x0, lang)
    return x0, x1, y0, y1


def _draw_links(ax, scenario: Scenario, zorder=2):
    """링크 화살표를 만들고 리스트로 돌려준다(프레임마다 스타일만 갱신)."""
    arrows = []
    seen = {}
    for L in scenario.links:
        pair = frozenset((L.source_idx, L.target_idx))
        k = seen.get(pair, 0)
        seen[pair] = k + 1
        rad = 0.16 if k == 0 else (-0.30 if k == 1 else 0.34)
        p0 = scenario.facilities[L.source_idx].map_xy
        p1 = scenario.facilities[L.target_idx].map_xy
        a = FancyArrowPatch(
            tuple(p0), tuple(p1),
            connectionstyle=f"arc3,rad={rad}",
            arrowstyle="-|>", mutation_scale=13,
            linewidth=1.1, color="#cdcbc4",
            shrinkA=21, shrinkB=23, zorder=zorder, alpha=0.85,
        )
        ax.add_patch(a)
        arrows.append((L, a))
    return arrows


def _style_link(arrow, rate, active, kmax_rate, frame):
    if not active or rate <= 1e-9:
        arrow.set_color("#cdcbc4")
        arrow.set_linewidth(1.1)
        arrow.set_alpha(0.55)
        arrow.set_linestyle("solid")
        arrow.set_mutation_scale(11)
    else:
        f = min(rate / kmax_rate, 1.0) if kmax_rate > 0 else 0.0
        arrow.set_color("#b32f24")
        arrow.set_linewidth(1.6 + 4.2 * f)
        arrow.set_alpha(0.92)
        # 흐름 방향이 보이도록 대시 위상을 프레임마다 밀어준다.
        arrow.set_linestyle((float(-frame * 2.2 % 9.0), (5.0, 4.0)))
        arrow.set_mutation_scale(14 + 8 * f)


def _draw_nodes(ax, scenario: Scenario, lang: str, *, base=1, compact=False):
    """도넛 노드(바깥=직접, 안쪽=복합) + 라벨. 갱신용 아티스트를 돌려준다."""
    xs = np.array([f.map_xy[0] for f in scenario.facilities], dtype=float)
    ys = np.array([f.map_xy[1] for f in scenario.facilities], dtype=float)
    s_out = (2050 if not compact else 1000) * base
    s_in = (980 if not compact else 470) * base
    # 평면도에는 표고축이 없으므로 심도를 테두리 선종류로 구분한다.
    # 실선 = 지상/지표, 점선 = 지하 매설·지하구조물.
    styles = ["dashed" if f.depth_m < 0 else "solid" for f in scenario.facilities]
    outer = ax.scatter(xs, ys, s=s_out, c=["#ffffff"] * len(xs),
                       edgecolors="#3b3a37", linewidths=1.4,
                       linestyle="solid", zorder=5)
    outer.set_linestyle([(0, (2.6, 1.9)) if st == "dashed" else (0, ())
                         for st in styles])
    inner = ax.scatter(xs, ys, s=s_in, c=["#ffffff"] * len(xs),
                       edgecolors="none", zorder=6)
    # 세로로 겹치는 노드는 이름표를 아래로 내려 서로 가리지 않게 한다.
    x_span = ax.get_xlim()[1] - ax.get_xlim()[0]
    y_span = ax.get_ylim()[1] - ax.get_ylim()[0]
    y_lo = ax.get_ylim()[0]
    below = []
    for i in range(len(xs)):
        blocked = any(
            j != i
            and abs(xs[j] - xs[i]) < 0.17 * x_span
            and 0 < (ys[j] - ys[i]) < 0.32 * y_span
            for j in range(len(xs))
        )
        # 아래로 내리면 판 밖으로 나가는 노드는 그대로 위에 둔다.
        if blocked and ys[i] - 0.085 * y_span < y_lo:
            blocked = False
        below.append(blocked)

    value_texts, name_texts = [], []
    for i, f in enumerate(scenario.facilities):
        value_texts.append(ax.text(xs[i], ys[i], "", ha="center", va="center",
                                   fontsize=8.6 if not compact else 6.6,
                                   fontweight="bold", zorder=7))
        if not compact:
            label = _short(f, lang)
            wrapped = "\n".join(textwrap.wrap(label, 8 if lang == "ko" else 13))
            marker = "" if f.node_kind == "facility" else (
                " ◇" if f.node_kind == "medium" else " ▽")
            if f.depth_m < 0:
                marker += f"\nGL-{abs(f.depth_m):g} m"
            elif f.depth_m > 0:
                marker += f"\nGL+{f.depth_m:g} m"
            # 화살표 위를 지나가도 읽히도록 배경판을 깐다.
            name_texts.append(ax.annotate(
                wrapped + marker, (xs[i], ys[i]),
                textcoords="offset points",
                xytext=(0, -29) if below[i] else (0, 29),
                ha="center", va="top" if below[i] else "bottom",
                fontsize=8.4, fontweight="bold", color=INK, zorder=8,
                bbox=dict(boxstyle="round,pad=0.2", fc=SURFACE,
                          ec="none", alpha=0.9)))
    return outer, inner, value_texts, name_texts


def _update_nodes(outer, inner, value_texts, direct_v, comp_v):
    outer.set_facecolor([risk_color(v) for v in direct_v])
    inner.set_facecolor([risk_color(v) for v in comp_v])
    for i, t in enumerate(value_texts):
        t.set_text(f"{comp_v[i]:.2f}")
        t.set_color(_text_on(comp_v[i]))


def _add_colorbar(fig, ax_pos, lang: str):
    cax = fig.add_axes(ax_pos)
    grad = np.linspace(0, 1, 256).reshape(1, -1)
    cax.imshow(grad, aspect="auto", cmap=RISK_CMAP, extent=[0, 1, 0, 1])
    cax.set_yticks([])
    cax.set_xticks([b[0] for b in BANDS] + [1.0])
    cax.set_xticklabels([f"{b[0]:.2f}" for b in BANDS] + ["1.00"], fontsize=7.5,
                        color=MUTED)
    cax.tick_params(length=0, pad=2)
    for spine in cax.spines.values():
        spine.set_color(SOIL_LINE)
        spine.set_linewidth(0.7)
    for lo, ko, en in BANDS[1:]:
        cax.axvline(lo, color="#ffffff", linewidth=1.2, alpha=0.85)
    for i, (lo, ko, en) in enumerate(BANDS):
        hi = BANDS[i + 1][0] if i + 1 < len(BANDS) else 1.0
        cax.text((lo + hi) / 2, 0.5, ko if lang == "ko" else en,
                 ha="center", va="center", fontsize=7.6, fontweight="bold",
                 color=_text_on((lo + hi) / 2))
    cax.set_title("복합위험도" if lang == "ko" else "Composite risk",
                  fontsize=8.8, color=SECOND, pad=4, loc="left")
    return cax


def _rank_panel(ax, scenario: Scenario, lang: str):
    """우측 순위 막대. 값은 프레임마다 갱신."""
    n = scenario.n
    ax.set_facecolor(SURFACE)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(-0.6, n - 0.4)
    ax.invert_yaxis()
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(SOIL_LINE)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="x", labelsize=8, colors=MUTED, length=0)
    ax.set_yticks([])
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_title("복합위험도 순위" if lang == "ko" else "Ranked composite risk",
                 fontsize=10.5, color=INK, fontweight="bold", loc="left", pad=8)
    bars_direct = ax.barh(range(n), [0] * n, height=0.32, color="#c9c7c0",
                          zorder=2)
    bars_comp = ax.barh(range(n), [0] * n, height=0.56, color="#cccccc", zorder=3)
    labels = [ax.text(0.012, i - 0.46, "", fontsize=8.6, color=INK,
                      fontweight="bold", va="center", zorder=4) for i in range(n)]
    values = [ax.text(0, i, "", fontsize=8.4, color=INK, va="center",
                      ha="left", zorder=4) for i in range(n)]
    return bars_direct, bars_comp, labels, values


def _update_rank(scenario, bars_direct, bars_comp, labels, values,
                 direct_v, comp_v, lang):
    order = np.argsort(-comp_v)
    for slot, idx in enumerate(order):
        c = comp_v[idx]
        bars_comp[slot].set_width(c)
        bars_comp[slot].set_color(risk_color(c))
        bars_direct[slot].set_width(direct_v[idx])
        labels[slot].set_text(_short(scenario.facilities[idx], lang))
        values[slot].set_text(f"{c:.3f}   (직접 {direct_v[idx]:.3f})"
                              if lang == "ko"
                              else f"{c:.3f}   (direct {direct_v[idx]:.3f})")
        # 막대 밖에 쓸 자리가 있으면 밖에, 없으면 막대 안에 흰 글씨로.
        if c <= 0.60:
            values[slot].set_x(c + 0.015)
            values[slot].set_ha("left")
            values[slot].set_color(INK)
        else:
            values[slot].set_x(c - 0.015)
            values[slot].set_ha("right")
            values[slot].set_color("#ffffff")


# --------------------------------------------------------------------------
# 1) 단일 지도 애니메이션
# --------------------------------------------------------------------------
def riskmap_gif(scenario: Scenario, result: Result, out_path: Path,
                *, lang: str = "ko", frame_count: int = 61, fps: int = 8):
    t_arr, comp, direct = result.times, result.composite, result.direct
    frames = np.linspace(scenario.t_start, scenario.t_end, frame_count)

    fig = plt.figure(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes([0.055, 0.145, 0.615, 0.66])
    axr = fig.add_axes([0.725, 0.145, 0.245, 0.66])
    _setup_map_axes(ax, scenario, lang)
    node_xy = [f.map_xy for f in scenario.facilities]
    field = basemap.RiskField(ax, node_xy, RISK_CMAP)
    glow = basemap.make_glow(ax, [p[0] for p in node_xy], [p[1] for p in node_xy])
    arrows = _draw_links(ax, scenario, zorder=3.6)
    outer, inner, vtexts, _ = _draw_nodes(ax, scenario, lang)
    bd, bc, blab, bval = _rank_panel(axr, scenario, lang)
    _add_colorbar(fig, [0.055, 0.055, 0.30, 0.026], lang)

    title = (f"{scenario.id} · 시설물 위험도 지도"
             if lang == "ko" else f"{scenario.id} · Facility risk map")
    fig.text(0.055, 0.945, title, fontsize=25, fontweight="bold", color=INK)
    fig.text(0.055, 0.905,
             (scenario.title_ko if lang == "ko" else scenario.title_en)
             + "  ·  " + (scenario.site["name_ko"] if lang == "ko"
                          else scenario.site["name_en"]),
             fontsize=12.5, color=SECOND)
    fig.text(0.055, 0.872,
             ("바깥 고리 = 직접위험도(상호의존 X) · 안쪽 원 = 복합위험도(O) · 점선 테두리 = 지하 시설물 · "
              "붉은 화살표 = 전달 발생 중, 굵기 ∝ 순간 전달률"
              if lang == "ko" else
              "Outer ring = direct risk · Inner disc = composite risk · "
              "Red arrow = active transfer, width proportional to rate"),
             fontsize=10.5, color=MUTED)
    fig.text(0.40, 0.062,
             (("◇ 매개 노드   ▽ 기능 노드   ·   평면도(2D). x = 동서, y = 남북 [m]"
               "   ·   " + scenario.site.get("origin_ko", ""))
              if lang == "ko" else
              "Plan view. x = east [m], y = north [m]; schematic positions."),
             fontsize=8.4, color=MUTED)
    fig.text(0.40, 0.036,
             ("시설물 위치는 공개 지점 기준의 개념 배치이며 실제 시설물 대장 좌표가 아니다."
              "   ·   기저도 © OpenStreetMap contributors (ODbL)"
              if lang == "ko" else
              "Facility positions are schematic, not surveyed asset coordinates."
              "   ·   Basemap © OpenStreetMap contributors (ODbL)"),
             fontsize=8.4, color=MUTED)

    clock = fig.text(0.97, 0.945, "", fontsize=23, fontweight="bold",
                     color=INK, ha="right")
    banner = fig.text(0.97, 0.905, "", fontsize=13, fontweight="bold",
                      color=SECOND, ha="right")

    kmax_rate = max(
        (max(_transfer_rate(scenario, L, comp, tt, t_arr) for tt in frames)
         for L in scenario.links), default=1.0)

    def update(fi):
        t = frames[fi]
        d = np.array([_at(t_arr, direct[i], t) for i in range(scenario.n)])
        c = np.array([_at(t_arr, comp[i], t) for i in range(scenario.n)])
        field.update(c)
        basemap.update_glow(glow, c, RISK_CMAP)
        _update_nodes(outer, inner, vtexts, d, c)
        _update_rank(scenario, bd, bc, blab, bval, d, c, lang)
        for L, a in arrows:
            rate = _transfer_rate(scenario, L, comp, t, t_arr)
            _style_link(a, rate, rate > 1e-9, kmax_rate, fi)
        prec = 2 if scenario.t_end <= 3 else 1
        clock.set_text(f"t = {t:0.{prec}f} h")
        past = [e for e in scenario.events if e["time_hour"] <= t + 1e-9]
        if past:
            banner.set_text(past[-1]["label_ko"] if lang == "ko"
                            else past[-1]["label_en"])
            banner.set_color(past[-1].get("color", SECOND))
        else:
            banner.set_text("재난 발생 전" if lang == "ko" else "Pre-disaster")
            banner.set_color(SECOND)
        return []

    anim = FuncAnimation(fig, update, frames=len(frames), interval=125, blit=False)
    anim.save(out_path, writer=PillowWriter(fps=fps), dpi=100,
              savefig_kwargs={"facecolor": SURFACE})
    plt.close(fig)


# --------------------------------------------------------------------------
# 2) 상호의존 고려 X / O 나란히 비교 애니메이션
# --------------------------------------------------------------------------
def riskmap_compare_gif(scenario: Scenario, result: Result, direct_only: Result,
                        out_path: Path, *, lang: str = "ko",
                        frame_count: int = 61, fps: int = 8):
    t_arr, comp, direct = result.times, result.composite, result.direct
    frames = np.linspace(scenario.t_start, scenario.t_end, frame_count)

    fig = plt.figure(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor(SURFACE)
    axL = fig.add_axes([0.045, 0.135, 0.435, 0.66])
    axR = fig.add_axes([0.525, 0.135, 0.435, 0.66])

    for ax, head in ((axL, "상호의존 고려 X" if lang == "ko" else "Without interdependency"),
                     (axR, "상호의존 고려 O" if lang == "ko" else "With interdependency")):
        _setup_map_axes(ax, scenario, lang)
        ax.set_title(head, fontsize=15, fontweight="bold", color=INK,
                     loc="left", pad=10)

    node_xy = [f.map_xy for f in scenario.facilities]
    fieldL = basemap.RiskField(axL, node_xy, RISK_CMAP)
    fieldR = basemap.RiskField(axR, node_xy, RISK_CMAP)
    glowL = basemap.make_glow(axL, [p[0] for p in node_xy], [p[1] for p in node_xy])
    glowR = basemap.make_glow(axR, [p[0] for p in node_xy], [p[1] for p in node_xy])
    arrowsR = _draw_links(axR, scenario, zorder=3.6)
    outL, inL, vL, _ = _draw_nodes(axL, scenario, lang)
    outR, inR, vR, _ = _draw_nodes(axR, scenario, lang)
    _add_colorbar(fig, [0.045, 0.052, 0.26, 0.026], lang)

    fig.text(0.045, 0.945,
             f"{scenario.id} · " + ("상호의존 반영 여부에 따른 위험도 지도 비교"
                                    if lang == "ko" else
                                    "Facility risk map — with vs without interdependency"),
             fontsize=23, fontweight="bold", color=INK)
    fig.text(0.045, 0.902,
             (scenario.title_ko if lang == "ko" else scenario.title_en),
             fontsize=12.5, color=SECOND)
    fig.text(0.045, 0.870,
             ("왼쪽은 H×E×V 직접위험도만, 오른쪽은 전이까지 반영한 값. 두 지도의 색 차이가 상호의존성지수 I."
              if lang == "ko" else
              "Left: direct risk H×E×V only. Right: transfer included. The colour gap is the interdependency index."),
             fontsize=10.5, color=MUTED)
    clock = fig.text(0.96, 0.945, "", fontsize=23, fontweight="bold",
                     color=INK, ha="right")
    banner = fig.text(0.96, 0.902, "", fontsize=13, fontweight="bold",
                      color=SECOND, ha="right")
    gap = fig.text(0.96, 0.870, "", fontsize=11.5, color="#b32f24",
                   ha="right", fontweight="bold")

    kmax_rate = max(
        (max(_transfer_rate(scenario, L, comp, tt, t_arr) for tt in frames)
         for L in scenario.links), default=1.0)

    def update(fi):
        t = frames[fi]
        d = np.array([_at(t_arr, direct[i], t) for i in range(scenario.n)])
        c = np.array([_at(t_arr, comp[i], t) for i in range(scenario.n)])
        fieldL.update(d); basemap.update_glow(glowL, d, RISK_CMAP)
        fieldR.update(c); basemap.update_glow(glowR, c, RISK_CMAP)
        _update_nodes(outL, inL, vL, d, d)
        _update_nodes(outR, inR, vR, d, c)
        for L, a in arrowsR:
            rate = _transfer_rate(scenario, L, comp, t, t_arr)
            _style_link(a, rate, rate > 1e-9, kmax_rate, fi)
        prec = 2 if scenario.t_end <= 3 else 1
        clock.set_text(f"t = {t:0.{prec}f} h")
        past = [e for e in scenario.events if e["time_hour"] <= t + 1e-9]
        banner.set_text((past[-1]["label_ko"] if lang == "ko" else past[-1]["label_en"])
                        if past else ("재난 발생 전" if lang == "ko" else "Pre-disaster"))
        banner.set_color(past[-1].get("color", SECOND) if past else SECOND)
        n_over = int(np.sum((c >= 0.5) & (d < 0.5)))
        gap.set_text(
            (f"상호의존 때문에 '경계' 이상으로 올라간 시설물 {n_over}개"
             if lang == "ko" else
             f"{n_over} facilities pushed past 0.5 by interdependency")
            if n_over else "")
        return []

    anim = FuncAnimation(fig, update, frames=len(frames), interval=125, blit=False)
    anim.save(out_path, writer=PillowWriter(fps=fps), dpi=100,
              savefig_kwargs={"facecolor": SURFACE})
    plt.close(fig)


# --------------------------------------------------------------------------
# 3) 시점별 스냅숏 (슬라이드용 정지 이미지)
# --------------------------------------------------------------------------
def riskmap_snapshots(scenario: Scenario, result: Result, out_path: Path,
                      *, lang: str = "ko", n_snap: int = 6):
    t_arr, comp, direct = result.times, result.composite, result.direct
    snaps = np.linspace(scenario.t_start, scenario.t_end, n_snap)
    ncol = 3
    nrow = int(np.ceil(n_snap / ncol))

    fig, axes = plt.subplots(nrow, ncol, figsize=(16, 4.3 * nrow + 2.6), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    axes = np.atleast_1d(axes).ravel()

    kmax_rate = max(
        (max(_transfer_rate(scenario, L, comp, tt, t_arr) for tt in snaps)
         for L in scenario.links), default=1.0)

    for k, t in enumerate(snaps):
        ax = axes[k]
        _setup_map_axes(ax, scenario, lang, compact=True)
        node_xy = [f.map_xy for f in scenario.facilities]
        fld = basemap.RiskField(ax, node_xy, RISK_CMAP, res=150)
        gl = basemap.make_glow(ax, [p[0] for p in node_xy], [p[1] for p in node_xy],
                               base=3000)
        arrows = _draw_links(ax, scenario, zorder=3.6)
        outer, inner, vtexts, _ = _draw_nodes(ax, scenario, lang, base=0.62)
        d = np.array([_at(t_arr, direct[i], t) for i in range(scenario.n)])
        c = np.array([_at(t_arr, comp[i], t) for i in range(scenario.n)])
        fld.update(c)
        basemap.update_glow(gl, c, RISK_CMAP)
        _update_nodes(outer, inner, vtexts, d, c)
        for L, a in arrows:
            rate = _transfer_rate(scenario, L, comp, t, t_arr)
            _style_link(a, rate, rate > 1e-9, kmax_rate, 0)
            if rate > 1e-9:
                a.set_linestyle("solid")
        prec = 2 if scenario.t_end <= 3 else 1
        past = [e for e in scenario.events if e["time_hour"] <= t + 1e-9]
        ev = (past[-1]["label_ko"] if lang == "ko" else past[-1]["label_en"]) if past \
            else ("재난 발생 전" if lang == "ko" else "Pre-disaster")
        ax.set_title(f"t = {t:0.{prec}f} h   ·   {ev}", fontsize=11.5,
                     fontweight="bold", color=INK, loc="left", pad=6)
    for k in range(n_snap, len(axes)):
        axes[k].axis("off")

    h = fig.get_size_inches()[1]
    fig.text(0.035, 1 - 0.42 / h,
             f"{scenario.id} · " + ("시점별 시설물 위험도 지도" if lang == "ko"
                                    else "Facility risk map over time"),
             fontsize=22, fontweight="bold", color=INK, va="center")
    fig.text(0.035, 1 - 0.78 / h,
             (scenario.title_ko if lang == "ko" else scenario.title_en)
             + "  ·  " + (scenario.site["name_ko"] if lang == "ko"
                          else scenario.site["name_en"]),
             fontsize=11.5, color=SECOND, va="center")
    fig.text(0.035, 1 - 1.05 / h,
             ("바깥 고리 = 직접위험도(상호의존 X) · 안쪽 원 = 복합위험도(O) · "
              "숫자 = 복합위험도 · 점선 테두리 = 지하 시설물 · 붉은 화살표 = 전달 발생 중"
              if lang == "ko" else
              "Outer ring = direct risk · Inner disc = composite risk · "
              "number = composite · red arrow = active transfer"),
             fontsize=10, color=MUTED, va="center")
    fig.text(0.035, 1 - 1.28 / h,
             "기저도 © OpenStreetMap contributors (ODbL) · 배경 음영 = 노드 위험도의 IDW 보간면"
             if lang == "ko" else
             "Basemap © OpenStreetMap contributors (ODbL) · shading = IDW interpolation",
             fontsize=8.8, color=MUTED, va="center")
    _add_colorbar(fig, [0.035, 0.30 / h, 0.26, 0.20 / h], lang)
    fig.subplots_adjust(left=0.035, right=0.975,
                        top=1 - 1.72 / h, bottom=0.80 / h,
                        hspace=0.24, wspace=0.09)
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)


# --------------------------------------------------------------------------
# 4) 공간 전파 분석
# --------------------------------------------------------------------------
THRESHOLDS = (0.25, 0.50, 0.75)


def propagation_table(scenario: Scenario, result: Result, direct_only: Result,
                      thresholds=THRESHOLDS) -> list[dict]:
    """임계 위험도 도달시간을 상호의존 고려 O/X 로 비교하고, 공간 전파속도를 낸다.

    시나리오마다 도달 가능한 위험도 수준이 다르므로(예: B·E 는 해석기간 안에
    0.5 에 도달하지 않는다) 여러 임계값에 대해 한 번에 계산한다.
    """
    trig_key = scenario.site["trigger_facility"]
    idx = {f.key: i for i, f in enumerate(scenario.facilities)}
    tx, ty = scenario.facilities[idx[trig_key]].map_xy
    t_arr = result.times

    def crossing(series, th):
        hit = np.nonzero(series >= th)[0]
        return float(t_arr[hit[0]]) if len(hit) else None

    rows = []
    for i, f in enumerate(scenario.facilities):
        dist = float(np.hypot(f.map_xy[0] - tx, f.map_xy[1] - ty))
        row = {
            "scenario": scenario.id,
            "facility_ko": f.name_ko,
            "facility_en": f.name_en,
            "is_trigger": int(f.key == trig_key),
            "directly_exposed": int(f.directly_exposed),
            "map_x_m": f.map_xy[0],
            "map_y_m": f.map_xy[1],
            "distance_from_trigger_m": round(dist, 1),
            "analysis_end_hour": scenario.t_end,
            "peak_composite": round(float(result.composite[i].max()), 6),
            "peak_direct_only": round(float(direct_only.composite[i].max()), 6),
        }
        for th in thresholds:
            tag = f"{th:.2f}"
            t_comp = crossing(result.composite[i], th)
            t_dir = crossing(direct_only.composite[i], th)
            t_trig = crossing(result.composite[idx[trig_key]], th)
            speed = ""
            if (t_comp is not None and t_trig is not None
                    and t_comp > t_trig and dist > 0):
                speed = round(dist / (t_comp - t_trig), 2)
            row[f"t_cross_{tag}_composite_h"] = t_comp if t_comp is not None else ""
            row[f"t_cross_{tag}_direct_only_h"] = t_dir if t_dir is not None else ""
            row[f"only_via_interdependency_{tag}"] = int(
                t_comp is not None and t_dir is None)
            row[f"time_gained_{tag}_h"] = (round(t_dir - t_comp, 3)
                                           if (t_comp is not None and t_dir is not None)
                                           else "")
            row[f"speed_{tag}_m_per_h"] = speed
        rows.append(row)
    return rows


def propagation_chart(all_rows: list[dict], out_path: Path, threshold: float = 0.25,
                      lang: str = "ko"):
    """거리 대 도달시간. 상호의존으로만 임계를 넘은 시설물을 강조한다.

    시나리오별 해석시간이 2h~72h 로 다르므로, y축은 **해석시간 대비 비율**로
    정규화해야 비교가 성립한다. 절대시간은 CSV 를 본다.
    """
    fig, ax = plt.subplots(figsize=(13.5, 7.4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    tag = f"{threshold:.2f}"
    key, only_key = f"t_cross_{tag}_composite_h", f"only_via_interdependency_{tag}"
    markers = {"Scenario A": "o", "Scenario B": "s", "Scenario C": "^",
               "Scenario E": "D", "Scenario F": "P"}
    pts = [r for r in all_rows if r[key] != "" and not r["is_trigger"]]
    pts.sort(key=lambda r: r["distance_from_trigger_m"])
    seen = set()
    placed = []          # 이미 쓴 라벨 위치 (데이터좌표) — 겹치면 반대편으로 돌린다
    for r in pts:
        only = r[only_key]
        sc = r["scenario"]
        x = r["distance_from_trigger_m"]
        y = r[key] / r["analysis_end_hour"]
        ax.scatter(x, y, s=175, marker=markers.get(sc, "o"),
                   facecolor="#b32f24" if only else "#ffffff",
                   edgecolor="#b32f24" if only else "#6b6a66",
                   linewidths=1.7, zorder=3,
                   label=sc if sc not in seen else None)
        seen.add(sc)
        near = sum(1 for (px, py) in placed
                   if abs(px - x) < 70 and abs(py - y) < 0.085)
        off = [(11, 5), (11, -13), (-11, 5), (-11, -13)][near % 4]
        ax.annotate(r["facility_ko"] if lang == "ko" else r["facility_en"],
                    (x, y), textcoords="offset points", xytext=off,
                    ha="left" if off[0] > 0 else "right",
                    fontsize=8.4, color=SECOND, zorder=4,
                    bbox=dict(boxstyle="round,pad=0.12", fc=SURFACE,
                              ec="none", alpha=0.75))
        placed.append((x, y))
    ax.set_xlabel("트리거 시설물로부터의 거리 [m]" if lang == "ko"
                  else "Distance from trigger facility [m]", fontsize=12)
    ax.set_ylabel(f"복합위험도 {threshold:.2f} 도달시점 (해석시간 대비 비율)"
                  if lang == "ko"
                  else f"Time to composite risk {threshold:.2f} (fraction of window)",
                  fontsize=12)
    ax.set_ylim(0, 1.06)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=11, ncol=5, loc="upper center",
              bbox_to_anchor=(0.5, -0.11))
    ax.set_title("위험 전파: 거리와 임계 도달시점" if lang == "ko"
                 else "Risk propagation: distance vs time to threshold",
                 fontsize=19, fontweight="bold", color=INK, loc="left", pad=34)
    ax.text(0, 1.055,
            ("채운 표식 = 상호의존을 고려해야만 임계를 넘는 시설물 (직접위험도만으로는 해석기간 내 미도달)"
             if lang == "ko" else
             "Filled marker = crosses the threshold only when interdependency is included"),
            transform=ax.transAxes, fontsize=10.5, color=MUTED)
    ax.text(0, 1.018,
            ("시나리오별 해석시간(2~72 h)이 달라 y축은 해석시간 대비 비율로 정규화했다."
             if lang == "ko" else
             "Analysis windows differ (2–72 h); y is normalised by each window."),
            transform=ax.transAxes, fontsize=9.6, color=MUTED)
    fig.subplots_adjust(left=0.085, right=0.975, top=0.84, bottom=0.20)
    fig.savefig(out_path, facecolor=SURFACE)
    plt.close(fig)

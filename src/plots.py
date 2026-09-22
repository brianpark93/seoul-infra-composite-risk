"""그림 생성: 위험도 타임라인 GIF, 위험도 구성 막대, G/K 네트워크 도식.

260917 gif 패키지(direct_facility_risk_timeline.gif / composite_risk_timeline.gif)
와 같은 레이아웃을 유지하되, 시나리오별 해석시간 축척과 이벤트를 따라간다.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import FancyArrowPatch

from .model import Result, Scenario

GRID = "#DDE3EA"
INK = "#172033"
MUTE = "#526071"
SPINE = "#475569"


def use_korean_font():
    """한글 라벨을 쓸 때 호출. Windows 기본 맑은 고딕."""
    plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams["axes.unicode_minus"] = False


def _facility_labels(scenario: Scenario, lang: str) -> list[str]:
    return [f.name_ko if lang == "ko" else f.name_en for f in scenario.facilities]


def _tick_step(span: float) -> float:
    """해석시간 폭에 맞는 x축 눈금 간격을 고른다."""
    for step in (0.1, 0.25, 0.5, 1, 2, 3, 4, 6, 8, 12, 24):
        if span / step <= 10:
            return step
    return span / 8.0


def _time_label(lang: str) -> str:
    return "경과시간 (h)" if lang == "ko" else "Elapsed time (hour)"


def _risk_label(lang: str) -> str:
    return "위험도 지수" if lang == "ko" else "Risk index"


# --------------------------------------------------------------------------
# 타임라인 GIF
# --------------------------------------------------------------------------
def timeline_gif(
    scenario: Scenario,
    result: Result,
    out_path: Path,
    *,
    with_interdependency: bool,
    lang: str = "en",
    frame_count: int = 49,
    fps: int = 8,
):
    labels = _facility_labels(scenario, lang)
    colors = [f.color for f in scenario.facilities]
    t = result.times
    comp = result.composite
    direct = result.direct

    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.set_xlim(scenario.t_start, scenario.t_end)
    ax.set_ylim(0, 1.03)

    step = _tick_step(scenario.t_end - scenario.t_start)
    ticks = np.arange(scenario.t_start, scenario.t_end + step / 2, step)
    ax.set_xticks(ticks)
    ax.set_yticks(np.arange(0, 1.01, 0.1))
    ax.tick_params(axis="both", labelsize=13)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_xlabel(_time_label(lang), fontsize=18, labelpad=14)
    ax.set_ylabel(_risk_label(lang), fontsize=18, labelpad=14)

    if with_interdependency:
        title = (
            f"{scenario.id} · 복합위험도 (상호의존 고려 O)"
            if lang == "ko"
            else f"{scenario.id} · Composite risk with interdependency"
        )
        subtitle = (
            "실선: 복합위험도  ·  점선: 직접위험도 H(t) x E(t) x V(t)"
            if lang == "ko"
            else "Solid: composite risk  ·  Dotted: direct risk H(t) x E(t) x V(t)"
        )
    else:
        title = (
            f"{scenario.id} · 직접위험도 (상호의존 고려 X)"
            if lang == "ko"
            else f"{scenario.id} · Direct risk without interdependency"
        )
        subtitle = (
            "직접위험도 H(t) x E(t) x V(t) 만 반영"
            if lang == "ko"
            else "Direct risk H(t) x E(t) x V(t) only"
        )

    ax.set_title(title, loc="left", fontsize=26, fontweight="bold", pad=64)
    ax.text(
        0, 1.085,
        scenario.title_ko if lang == "ko" else scenario.title_en,
        transform=ax.transAxes, fontsize=13.5, color=MUTE,
    )
    ax.text(0, 1.030, subtitle, transform=ax.transAxes, fontsize=14.5, color=MUTE)
    for spine in ax.spines.values():
        spine.set_color(SPINE)

    solid, dotted, dots = {}, {}, {}
    for i, label in enumerate(labels):
        if with_interdependency:
            dotted[i], = ax.plot([], [], color=colors[i], linewidth=2,
                                 linestyle=(0, (4, 4)), alpha=0.32)
        solid[i], = ax.plot([], [], color=colors[i], linewidth=4, label=label)
        dots[i], = ax.plot([], [], "o", color=colors[i], markerfacecolor="white",
                           markeredgewidth=2.5, markersize=7)
    # 범례는 그래프 아래로 뺀다. 상단에 두면 이벤트 라벨과 겹친다.
    ncol = min(len(labels), 4)
    legend_rows = -(-len(labels) // ncol)          # ceil
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.135), ncol=ncol,
              frameon=False, fontsize=13.5)

    clock = ax.text(0.985, 1.085, "", transform=ax.transAxes, ha="right",
                    va="center", fontsize=21, fontweight="bold", color=INK)
    # 상태 문구는 범례 줄 수만큼 더 내린다.
    status = ax.text(0.5, -0.255 - 0.075 * legend_rows, "", transform=ax.transAxes,
                     ha="center", va="center", fontsize=17, fontweight="bold", color=INK)
    if with_interdependency:
        form = ("G(t) x K x max(R_source - R_target, 0)"
                if scenario.transfer_form == "diffusive"
                else "G(t) x K x R_source x (1 - R_target)")
        fig.text(0.11, 0.028, f"Transfer term: {form}", fontsize=12.5, color=MUTE)

    events = scenario.events
    event_artists = []
    frame_times = np.linspace(scenario.t_start, scenario.t_end, frame_count)

    def update(fi):
        t_now = frame_times[fi]
        mask = t <= t_now + 1e-9
        if not np.any(mask):
            mask[0] = True
        artists = [clock, status]
        for i in range(len(labels)):
            solid[i].set_data(t[mask], comp[i, mask])
            if with_interdependency:
                dotted[i].set_data(t[mask], direct[i, mask])
                artists.append(dotted[i])
            dots[i].set_data([t_now], [np.interp(t_now, t, comp[i])])
            artists += [solid[i], dots[i]]
        while event_artists:
            event_artists.pop().remove()
        active = [e for e in events if e["time_hour"] <= t_now + 1e-9]
        for e in active:
            ln = ax.axvline(e["time_hour"], color=e.get("color", SPINE),
                            linewidth=1.8, linestyle=(0, (5, 4)), alpha=0.8)
            label = e["label_ko"] if lang == "ko" else e["label_en"]
            tx = ax.text(e["time_hour"] + (scenario.t_end - scenario.t_start) * 0.007,
                         e.get("label_y", 0.93), label, rotation=90, ha="left",
                         va="top", fontsize=11.5, fontweight="bold",
                         color=e.get("color", SPINE))
            event_artists.extend([ln, tx])
        artists += event_artists
        unit = "h"
        prec = 2 if scenario.t_end <= 3 else 1
        clock.set_text(f"t = {t_now:0.{prec}f} {unit}")
        if active:
            status.set_text(active[-1]["label_ko"] if lang == "ko"
                            else active[-1]["label_en"])
            status.set_color(active[-1].get("color", INK))
        else:
            status.set_text("재난 발생 전" if lang == "ko" else "Pre-disaster baseline")
            status.set_color(INK)
        return artists

    anim = FuncAnimation(fig, update, frames=len(frame_times), interval=120, blit=False)
    fig.subplots_adjust(left=0.11, right=0.94, top=0.755, bottom=0.335)
    anim.save(out_path, writer=PillowWriter(fps=fps), dpi=100,
              savefig_kwargs={"facecolor": "white"})
    plt.close(fig)


# --------------------------------------------------------------------------
# 최종 시점 위험도 구성 막대
# --------------------------------------------------------------------------
def composition_bar(scenario: Scenario, result: Result, out_path: Path, lang: str = "en"):
    labels = _facility_labels(scenario, lang)
    direct = result.direct[:, -1]
    inter = result.interdependency[:, -1]
    total = result.composite[:, -1]
    capped = result.cap_adjustment[:, -1]

    fig, ax = plt.subplots(figsize=(13.5, 7.5), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    x = np.arange(len(labels))
    ax.bar(x, direct, width=0.62, color="#64748B",
           label="직접위험도" if lang == "ko" else "Direct risk")
    ax.bar(x, inter, width=0.62, bottom=direct, color="#F97316",
           label="상호의존성지수" if lang == "ko" else "Interdependency index")
    ax.set_ylim(0, 1.10)
    ax.set_ylabel(_risk_label(lang), fontsize=15)
    ax.set_xticks(x, labels, fontsize=12)
    ax.tick_params(axis="y", labelsize=12)
    title = (
        f"{scenario.id} · t = {scenario.t_end:g} h 시점 복합위험도 구성"
        if lang == "ko"
        else f"{scenario.id} · Composite risk composition at t = {scenario.t_end:g} h"
    )
    ax.set_title(title, loc="left", fontsize=21, fontweight="bold", pad=18)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=13, ncol=2, loc="upper left")

    for i, (d, inc, tot) in enumerate(zip(direct, inter, total)):
        if d > 0.05:
            ax.text(i, d / 2, f"{d:.3f}", ha="center", va="center",
                    color="white", fontsize=11, fontweight="bold")
        if inc > 0.05:
            ax.text(i, d + inc / 2, f"+{inc:.3f}", ha="center", va="center",
                    color="white", fontsize=11, fontweight="bold")
        ax.text(i, min(tot + 0.02, 1.06), f"{tot:.3f}", ha="center", va="bottom",
                fontsize=13, fontweight="bold")

    note = (
        "복합위험도 = min(1, 직접위험도 + 상호의존성지수)"
        if lang == "ko"
        else "Composite risk = min(1, direct risk + interdependency index)"
    )
    clipped = [labels[i] for i in range(len(labels)) if capped[i] < -1e-6]
    if clipped:
        note += ("  ·  상한 1.0 에서 제한된 시설물: " if lang == "ko"
                 else "  ·  Capped at 1.0: ") + ", ".join(clipped)
    ax.text(0.01, -0.13, note, transform=ax.transAxes, fontsize=11, color=MUTE)
    fig.subplots_adjust(left=0.10, right=0.97, top=0.86, bottom=0.20)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)


# --------------------------------------------------------------------------
# G/K 네트워크 도식
# --------------------------------------------------------------------------
def network_diagram(scenario: Scenario, out_path: Path, lang: str = "en"):
    fig, ax = plt.subplots(figsize=(13.5, 7.8), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.axis("off")

    pos = {i: f.layout for i, f in enumerate(scenario.facilities)}
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    ax.set_xlim(min(xs) - 1.05, max(xs) + 1.45)
    ax.set_ylim(min(ys) - 0.85, max(ys) + 0.85)

    kmax = max((L.K for L in scenario.links), default=1.0)

    # 같은 노드쌍(정/역방향 포함)의 링크가 겹치지 않도록 곡률과 라벨 위치를 나눈다.
    pair_count: dict[frozenset, int] = {}
    for L in scenario.links:
        pair_count[frozenset((L.source_idx, L.target_idx))] = (
            pair_count.get(frozenset((L.source_idx, L.target_idx)), 0) + 1
        )
    seen: dict[frozenset, int] = {}

    for L in scenario.links:
        pair = frozenset((L.source_idx, L.target_idx))
        k = seen.get(pair, 0)
        total = pair_count[pair]
        seen[pair] = k + 1

        # 링크가 1개면 살짝만 휘고, 여러 개면 부호를 번갈아 크게 벌린다.
        if total == 1:
            curve = 0.14
        else:
            curve = (0.26 + 0.20 * (k // 2)) * (1 if k % 2 == 0 else -1)
        # 같은 방향의 병렬 링크는 라벨을 곡선 위 서로 다른 지점에 둔다.
        s_param = 0.5 if total == 1 else (0.34 + 0.32 * (k % 2))

        x0, y0 = pos[L.source_idx]
        x1, y1 = pos[L.target_idx]
        lw = 1.2 + 4.0 * (L.K / kmax)
        ax.add_patch(FancyArrowPatch(
            (x0, y0), (x1, y1),
            connectionstyle=f"arc3,rad={curve}",
            arrowstyle="-|>", mutation_scale=17,
            linewidth=lw, color="#94A3B8",
            shrinkA=33, shrinkB=35, zorder=1, alpha=0.9,
        ))

        # matplotlib 의 arc3 는 2차 베지에다. 제어점을 구해 곡선 위 정확한 점에 라벨을 놓는다.
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        cx, cy = mx - curve * (y1 - y0), my + curve * (x1 - x0)
        s = s_param
        lx = (1 - s) ** 2 * x0 + 2 * (1 - s) * s * cx + s ** 2 * x1
        ly = (1 - s) ** 2 * y0 + 2 * (1 - s) * s * cy + s ** 2 * y1
        ax.text(lx, ly, f"K={L.K:.3f}\nt≥{L.activation_time_hour:g}h",
                ha="center", va="center", fontsize=8.5, color="#334155",
                bbox=dict(boxstyle="round,pad=0.24", fc="white",
                          ec="#CBD5E1", lw=0.7), zorder=3)

    kind_edge = {"facility": "#0F172A", "medium": "#92400E", "function": "#1E3A8A"}
    for i, f in enumerate(scenario.facilities):
        x, y = pos[i]
        ax.scatter([x], [y], s=2900, c=f.color, alpha=0.20, zorder=2,
                   edgecolors=kind_edge.get(f.node_kind, "#0F172A"),
                   linewidths=1.8 if f.directly_exposed else 1.0,
                   linestyle="-" if f.directly_exposed else "--")
        label = f.name_ko if lang == "ko" else f.name_en
        width = 7 if lang == "ko" else 12
        wrapped = "\n".join(textwrap.wrap(label, width=width)) or label
        ax.text(x, y, wrapped, ha="center", va="center",
                fontsize=9.0, fontweight="bold", color=INK, zorder=4)

    title = (
        f"{scenario.id} · 시설물 간 연결관계 (G·K)"
        if lang == "ko"
        else f"{scenario.id} · Inter-facility dependency network (G, K)"
    )
    ax.set_title(title, loc="left", fontsize=20, fontweight="bold", pad=14)
    note = (
        "실선 테두리 = 재난에 직접 노출 · 점선 테두리 = 전이로만 위험 증가 · 화살표 굵기 ∝ K"
        if lang == "ko"
        else "Solid outline = directly exposed · Dashed = risk from transfer only · "
             "Arrow width proportional to K"
    )
    ax.text(0.0, -0.035, note, transform=ax.transAxes, fontsize=10.5, color=MUTE)
    fig.subplots_adjust(left=0.03, right=0.97, top=0.90, bottom=0.07)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)


# --------------------------------------------------------------------------
# 시나리오 간 비교
# --------------------------------------------------------------------------
def cross_scenario_bar(rows: list[dict], out_path: Path, lang: str = "en"):
    """시나리오별 상호의존 증폭량(평균/최대)을 비교한다."""
    labels = [f"{r['scenario']}\n{r['facility']}" for r in rows]
    direct = np.array([r["direct"] for r in rows])
    inter = np.array([r["interdependency"] for r in rows])

    fig, ax = plt.subplots(figsize=(15, 7.6), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    x = np.arange(len(rows))
    ax.bar(x, direct, width=0.66, color="#64748B",
           label="직접위험도" if lang == "ko" else "Direct risk")
    ax.bar(x, inter, width=0.66, bottom=direct, color="#F97316",
           label="상호의존성지수" if lang == "ko" else "Interdependency index")
    ax.set_ylim(0, 1.12)
    ax.set_ylabel("위험도 지수" if lang == "ko" else "Risk index", fontsize=14)
    ax.set_xticks(x, labels, fontsize=9.5)
    ax.grid(axis="y", color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=13, ncol=2, loc="upper left")
    title = (
        "시나리오별 상호의존영향이 가장 큰 시설물"
        if lang == "ko"
        else "Most interdependency-amplified facility per scenario"
    )
    ax.set_title(title, loc="left", fontsize=20, fontweight="bold", pad=16)
    for i, r in enumerate(rows):
        ax.text(i, min(r["composite"] + 0.02, 1.08), f"{r['composite']:.3f}",
                ha="center", va="bottom", fontsize=12, fontweight="bold")
    fig.subplots_adjust(left=0.07, right=0.98, top=0.87, bottom=0.16)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)

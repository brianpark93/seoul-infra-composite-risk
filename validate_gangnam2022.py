"""시나리오 G 검증 — 모형 결과 vs 2022-08-08 실제 관측 사건.

관측 기준점 (data/gangnam2022_sources.md)
    20:00 (t=4.0)  강남 일대 도로 차량 다수 침수 시작
    22:00 (t=6.0)  강남구 최대 시간강우 116 mm  (21:34-22:34)
    22:49 (t=6.82) 효성해링턴타워 인근 맨홀 인명사고

검증 지표
    1) 도로 위험도가 '주의'(0.25)를 넘는 시각  vs  관측 침수 개시 20:00
    2) 맨홀 위험도 급등 시각                vs  관측 맨홀사고 22:49
    3) 전달항 형식(diffusive / saturating)에 따른 차이
       — 서울시 공식 원인 ③(반포천 통수능 부족) 링크가 실제로 발현하는지
"""

from __future__ import annotations

import copy
import csv
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from src import riskmap
from src.model import Scenario, solve, solve_direct_only

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs" / "scenario_G"
SPEC = ROOT / "scenarios" / "scenario_G_gangnam2022.json"
THRESH = 0.25


def clock(t):
    """t=0 이 8/8 16:00."""
    total = 16 * 60 + int(round(t * 60))
    d = "8/9 " if total >= 24 * 60 else ""
    total %= 24 * 60
    return f"{d}{total // 60:02d}:{total % 60:02d}"


def crossing(times, series, th):
    hit = np.nonzero(series >= th)[0]
    return float(times[hit[0]]) if len(hit) else None


def run(spec, form):
    s = copy.deepcopy(spec)
    s["analysis"]["transfer_form"] = form
    sc = Scenario(s)
    return sc, solve(sc)


def main():
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    sc_d, res_d = run(spec, "diffusive")
    sc_s, res_s = run(spec, "saturating")
    dir_only = solve_direct_only(sc_d)
    idx = {f.key: i for i, f in enumerate(sc_d.facilities)}
    t = res_d.times

    print("=" * 74)
    print("시나리오 G 검증 — 강남역 2022-08-08 호우, 모형 vs 관측")
    print("=" * 74)
    print(f"해석창 8/8 16:00 – 8/9 02:00 · 임계 복합위험도 {THRESH}")

    rows = []
    checks = [
        ("road", 4.0, "20:00", "도로 차량 다수 침수 시작"),
        ("manhole", 6.82, "22:49", "효성해링턴타워 인근 맨홀 인명사고"),
    ]
    print(f"\n{'시설물':<16s} {'관측사건':<22s} {'관측시각':>8s} "
          f"{'모형(전이O)':>11s} {'모형(전이X)':>11s} {'오차':>8s}")
    for key, t_obs, clk, fact in checks:
        i = idx[key]
        tc = crossing(t, res_d.composite[i], THRESH)
        td = crossing(t, dir_only.composite[i], THRESH)
        err = (tc - t_obs) if tc is not None else None
        rows.append(dict(
            facility=sc_d.facilities[i].name_ko, observed_fact=fact,
            observed_clock=clk, observed_t_hour=t_obs,
            model_composite_t_hour=tc if tc is not None else "",
            model_composite_clock=clock(tc) if tc is not None else "미도달",
            model_direct_only_t_hour=td if td is not None else "",
            model_direct_only_clock=clock(td) if td is not None else "미도달",
            error_hour=round(err, 2) if err is not None else "",
        ))
        print(f"{sc_d.facilities[i].name_ko[:15]:<16s} {fact[:21]:<22s} {clk:>8s} "
              f"{(clock(tc) if tc is not None else '미도달'):>11s} "
              f"{(clock(td) if td is not None else '미도달'):>11s} "
              f"{(f'{err:+.2f} h' if err is not None else '-'):>8s}")

    # 맨홀 위험도 상승률이 최대가 되는 시각 (사고 시점과 대조)
    i = idx["manhole"]
    grad = np.gradient(res_d.composite[i], t)
    t_steep = float(t[int(np.argmax(grad))])
    print(f"\n맨홀 위험도 상승률 최대 시각 : {clock(t_steep)}  (t={t_steep:.2f} h)")
    print(f"  관측 맨홀사고               : 22:49  (t=6.82 h)")
    print(f"  차이                        : {t_steep - 6.82:+.2f} h")

    print("\n" + "-" * 74)
    print("전달항 형식별 — 서울시 공식 침수원인 링크의 실제 발현 여부")
    print("-" * 74)
    cause = {"banpo_to_sewer": "③ 반포천 상류 통수능 부족",
             "sewer_to_road": "②④ 역경사 관로 시공오류",
             "detention_to_road": "대책시설(용허리 저류조) 용량 소진",
             "road_to_station": "역사 출입구 유입",
             "road_to_manhole": "보도 침수",
             "sewer_to_manhole": "관 압력상승 → 맨홀 역류"}
    link_rows = []
    for L in sc_d.links:
        gd = res_d.link_G[L.key][t >= L.activation_time_hour]
        gs = res_s.link_G[L.key][t >= L.activation_time_hour]
        fd = float(gd.mean()) if len(gd) else 0.0
        fs = float(gs.mean()) if len(gs) else 0.0
        link_rows.append(dict(
            link=L.key, cause=cause.get(L.key, ""), K=round(L.K, 4),
            G_on_diffusive=round(fd, 3), G_on_saturating=round(fs, 3),
            cum_diffusive=round(float(res_d.link_cum[L.key][-1]), 4),
            cum_saturating=round(float(res_s.link_cum[L.key][-1]), 4)))
        flag = "  ← diffusive 에서 미발현" if fd == 0 else ""
        print(f"  {cause.get(L.key, L.key)[:28]:<30s} K={L.K:.3f}  "
              f"G(diff)={fd*100:5.1f}%  G(sat)={fs*100:5.1f}%{flag}")

    print("\n" + "-" * 74)
    print(f"{'시설물':<22s} {'직접위험 피크':>12s} {'복합 피크(diff)':>15s} {'복합 피크(sat)':>14s}")
    fac_rows = []
    for i, f in enumerate(sc_d.facilities):
        pd_, pc, ps = (float(dir_only.composite[i].max()),
                       float(res_d.composite[i].max()),
                       float(res_s.composite[i].max()))
        fac_rows.append(dict(facility=f.name_ko, direct_peak=round(pd_, 4),
                             composite_peak_diffusive=round(pc, 4),
                             composite_peak_saturating=round(ps, 4)))
        print(f"{f.name_ko[:21]:<22s} {pd_:>12.3f} {pc:>15.3f} {ps:>14.3f}")

    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in (("G_validation_timeline.csv", rows),
                       ("G_validation_links.csv", link_rows),
                       ("G_validation_facilities.csv", fac_rows)):
        with (OUT / name).open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0].keys()))
            w.writeheader()
            w.writerows(data)

    # ---------------- 검증 그래프 ----------------
    riskmap.use_korean_font()
    fig, ax = plt.subplots(figsize=(15, 8), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    for i, f in enumerate(sc_d.facilities):
        ax.plot(t, res_d.composite[i], linewidth=3, color=f.color, label=f.short_ko)
        ax.plot(t, dir_only.composite[i], linewidth=1.6, color=f.color,
                linestyle=(0, (4, 4)), alpha=0.35)
    ax.axhline(THRESH, color="#898781", linewidth=1.2, linestyle=(0, (2, 3)))
    ax.text(0.06, THRESH + 0.012, f"'주의' 임계 {THRESH}", fontsize=9.5, color="#52514e")

    for t_obs, clk, txt in [(4.0, "20:00", "관측: 도로 침수 개시"),
                            (6.0, "22:00", "관측: 최대 시간강우 116 mm"),
                            (6.82, "22:49", "관측: 맨홀 인명사고")]:
        ax.axvline(t_obs, color="#b32f24", linewidth=1.8, linestyle=(0, (5, 4)),
                   alpha=0.85)
        ax.text(t_obs + 0.07, 0.97, f"{clk}  {txt}", rotation=90, ha="left",
                va="top", fontsize=10, fontweight="bold", color="#b32f24")

    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.0)
    ax.set_xticks(range(11))
    ax.set_xticklabels([clock(v) for v in range(11)], fontsize=10)
    ax.set_xlabel("2022-08-08 → 08-09  (해석창 16:00–02:00)", fontsize=13, labelpad=10)
    ax.set_ylabel("복합위험도", fontsize=13)
    ax.grid(color="#e1e0d9", linewidth=0.9)
    ax.set_axisbelow(True)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.legend(frameon=False, fontsize=11, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.14))
    ax.set_title("시나리오 G 검증 — 강남역 2022-08-08 호우, 모형 vs 관측 사건",
                 fontsize=20, fontweight="bold", color="#0b0b0b", loc="left", pad=34)
    ax.text(0, 1.045,
            "실선 = 복합위험도(상호의존 O) · 점선 = 직접위험도만 · "
            "붉은 세로선 = 실제 관측된 사건 시각",
            transform=ax.transAxes, fontsize=11, color="#898781")
    fig.subplots_adjust(left=0.07, right=0.98, top=0.85, bottom=0.22)
    fig.savefig(OUT / "G_validation.png", facecolor="#fcfcfb")
    plt.close(fig)
    print(f"\n출력: {OUT}")


if __name__ == "__main__":
    main()

"""전달항 형태 민감도 분석: diffusive(현재식) vs saturating(옵션 C).

배경
    상호의존성지수_HeatEquation_설명자료.pptx slide 14 는 전달항을 세 가지로 제시한다.
        A  K(S_j - S_i)          증감 모두 허용, 네트워크 평형
        B  K[S_j - S_i]+         감소 금지, 전파 중심            <- 260918 구현식
        C  K S_j (1 - S_i)       증폭 허용, 상한 자연 포화

    현재 구현(B)은 R_source > R_target 일 때만 전달하므로,
    되먹임 링크(지반→굴착, 제연설비→터널 등)가 구조적으로 발현하지 않는다.
    또 상한 초과분을 hard clip 으로 잘라내므로 도로 위험도 1.033 -> 1.000 같은
    인위적 절단이 생긴다. C 는 두 문제를 모두 피한다.

이 스크립트는 같은 입력(H·E·V, G, K)에 대해 두 형식의 결과를 나란히 비교한다.

사용법
    python compare_transfer_forms.py
"""

from __future__ import annotations

import copy
import csv
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from src.model import Scenario, solve

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
FILES = {
    "A": "scenario_A.json",
    "B": "scenario_B.json",
    "C": "scenario_C.json",
    "E": "scenario_E.json",
    "F": "scenario_F_validation.json",
}


def run(spec: dict, form: str):
    s = copy.deepcopy(spec)
    s["analysis"]["transfer_form"] = form
    sc = Scenario(s)
    return sc, solve(sc)


def main():
    rows = []
    dormant_rows = []
    for tag, fname in FILES.items():
        spec = json.loads((ROOT / "scenarios" / fname).read_text(encoding="utf-8"))
        sc_d, res_d = run(spec, "diffusive")
        sc_s, res_s = run(spec, "saturating")

        print(f"\n=== {sc_d.id} — {sc_d.title_ko} ===")
        print(f"  {'시설물':<20s} {'diffusive':>10s} {'saturating':>11s} {'차이':>8s}")
        for i, f in enumerate(sc_d.facilities):
            a = float(res_d.composite[i, -1])
            b = float(res_s.composite[i, -1])
            print(f"  {f.name_ko:<20s} {a:>10.3f} {b:>11.3f} {b - a:>+8.3f}")
            rows.append({
                "scenario": sc_d.id,
                "facility_ko": f.name_ko,
                "facility_en": f.name_en,
                "direct_final": round(float(res_d.direct[i, -1]), 6),
                "composite_diffusive": round(a, 6),
                "composite_saturating": round(b, 6),
                "difference": round(b - a, 6),
                "capped_diffusive": round(float(-res_d.cap_adjustment[i, -1]), 6),
                "capped_saturating": round(float(-res_s.cap_adjustment[i, -1]), 6),
            })

        for L in sc_d.links:
            fd = res_d.link_G[L.key][res_d.times >= L.activation_time_hour]
            fs = res_s.link_G[L.key][res_s.times >= L.activation_time_hour]
            d_on = float(fd.mean()) if len(fd) else 0.0
            s_on = float(fs.mean()) if len(fs) else 0.0
            if d_on == 0.0:
                print(f"  [되살아난 링크] {L.source_name} → {L.target_name}: "
                      f"diffusive 0% → saturating {s_on * 100:.0f}% "
                      f"(누적전달 {res_s.link_cum[L.key][-1]:.4f})")
            dormant_rows.append({
                "scenario": sc_d.id, "link_key": L.key,
                "source": sc_d.facilities[L.source_idx].name_ko,
                "target": sc_d.facilities[L.target_idx].name_ko,
                "K_per_hour": round(L.K, 6),
                "G_on_fraction_diffusive": round(d_on, 4),
                "G_on_fraction_saturating": round(s_on, 4),
                "cum_transfer_diffusive": round(float(res_d.link_cum[L.key][-1]), 6),
                "cum_transfer_saturating": round(float(res_s.link_cum[L.key][-1]), 6),
            })

    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "sensitivity_transfer_form_facilities.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    p2 = OUT / "sensitivity_transfer_form_links.csv"
    with p2.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(dormant_rows[0].keys()))
        w.writeheader(); w.writerows(dormant_rows)

    # 비교 그래프
    labels = [f"{r['scenario'].replace('Scenario ', '')}·{r['facility_en']}" for r in rows]
    a = np.array([r["composite_diffusive"] for r in rows])
    b = np.array([r["composite_saturating"] for r in rows])
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(17, 7.5), dpi=150)
    fig.patch.set_facecolor("white"); ax.set_facecolor("white")
    ax.bar(x - 0.2, a, width=0.4, color="#64748B", label="diffusive  K·max(Rj−Ri,0)  [260918]")
    ax.bar(x + 0.2, b, width=0.4, color="#F97316", label="saturating  K·Rj·(1−Ri)  [option C]")
    ax.set_xticks(x, labels, rotation=55, ha="right", fontsize=8)
    ax.set_ylabel("Composite risk at end of analysis", fontsize=13)
    ax.set_ylim(0, 1.08)
    ax.grid(axis="y", color="#DDE3EA", linewidth=1); ax.set_axisbelow(True)
    ax.legend(frameon=False, fontsize=12, loc="upper left")
    ax.set_title("Transfer-term sensitivity: diffusive vs saturating",
                 loc="left", fontsize=19, fontweight="bold", pad=14)
    fig.subplots_adjust(left=0.06, right=0.99, top=0.90, bottom=0.30)
    fig.savefig(OUT / "sensitivity_transfer_form.png", facecolor="white")
    plt.close(fig)
    print(f"\n출력: {p.name}, {p2.name}, sensitivity_transfer_form.png")


if __name__ == "__main__":
    main()

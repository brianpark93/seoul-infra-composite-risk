"""2D 공간 위험도 지도 생성 및 공간 전파 분석.

사용법
    python run_maps.py                  # 전체 시나리오, 지도 GIF + 스냅숏 + 전파분석
    python run_maps.py --only A C
    python run_maps.py --no-gif         # 스냅숏 PNG 와 전파분석만 (빠름)
    python run_maps.py --lang en
    python run_maps.py --threshold 0.5  # 화면출력/그래프용 임계값
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from src import riskmap
from src.model import Scenario, solve, solve_direct_only

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
ORDER = ["A", "B", "C", "E", "F", "G"]
FILES = {"A": "scenario_A.json", "B": "scenario_B.json", "C": "scenario_C.json",
         "E": "scenario_E.json", "F": "scenario_F_validation.json",
         "G": "scenario_G_gangnam2022.json"}


def run_one(tag: str, *, make_gif: bool, lang: str, threshold: float):
    spec = json.loads((ROOT / "scenarios" / FILES[tag]).read_text(encoding="utf-8"))
    sc = Scenario(spec)
    res = solve(sc)
    dir_only = solve_direct_only(sc)
    out = OUT / f"scenario_{tag}"
    out.mkdir(parents=True, exist_ok=True)

    riskmap.riskmap_snapshots(sc, res, out / f"{tag}_riskmap_snapshots.png", lang=lang)
    if make_gif:
        riskmap.riskmap_gif(sc, res, out / f"{tag}_riskmap.gif", lang=lang)
        riskmap.riskmap_compare_gif(sc, res, dir_only,
                                    out / f"{tag}_riskmap_compare.gif", lang=lang)

    rows = riskmap.propagation_table(sc, res, dir_only)
    with (out / f"{tag}_propagation.csv").open("w", encoding="utf-8-sig",
                                               newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    tag_th = f"{threshold:.2f}"
    kc = f"t_cross_{tag_th}_composite_h"
    kd = f"t_cross_{tag_th}_direct_only_h"
    ks = f"speed_{tag_th}_m_per_h"
    ko = f"only_via_interdependency_{tag_th}"

    print(f"\n=== {sc.id} — {sc.title_ko} ===")
    print(f"  트리거 {sc.site['trigger_facility']}  ·  해석시간 {sc.t_end:g} h"
          f"  ·  아래 표의 임계 복합위험도 {tag_th}")
    print(f"  {'시설물':<18s} {'거리[m]':>8s} {'피크':>7s} {'고려O[h]':>9s} "
          f"{'고려X[h]':>9s} {'전파속도[m/h]':>13s}")
    for r in rows:
        tc = f"{r[kc]:.2f}" if r[kc] != "" else "미도달"
        td = f"{r[kd]:.2f}" if r[kd] != "" else "미도달"
        sp = str(r[ks]) if r[ks] != "" else "-"
        mark = "  ←전이로만 도달" if r[ko] else ""
        print(f"  {r['facility_ko']:<18s} {r['distance_from_trigger_m']:>8.1f} "
              f"{r['peak_composite']:>7.3f} {tc:>9s} {td:>9s} {sp:>13s}{mark}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, metavar="TAG")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--lang", choices=["ko", "en"], default="ko")
    ap.add_argument("--threshold", type=float, default=0.25,
                    help="화면출력·전파그래프용 임계값. CSV 에는 0.25/0.50/0.75 전부 기록")
    args = ap.parse_args()

    if args.lang == "ko":
        riskmap.use_korean_font()

    tags = [t.upper() for t in (args.only or ORDER)]
    OUT.mkdir(parents=True, exist_ok=True)
    all_rows = []
    for t in tags:
        all_rows += run_one(t, make_gif=not args.no_gif, lang=args.lang,
                            threshold=args.threshold)

    p = OUT / "summary_propagation.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)
    riskmap.propagation_chart(all_rows, OUT / "summary_propagation.png",
                              args.threshold, args.lang)

    print("\n=== 종합 — 임계값별, 상호의존을 고려해야만 넘어서는 시설물 ===")
    n = len(all_rows)
    for th in riskmap.THRESHOLDS:
        tg = f"{th:.2f}"
        only = [r for r in all_rows if r[f"only_via_interdependency_{tg}"]]
        print(f"\n  복합위험도 {tg} 이상 : {len(only)} / {n}개 시설물")
        for r in only:
            print(f"      {r['scenario']:<12s} {r['facility_ko']:<18s}"
                  f"  직접위험도 피크 {r['peak_direct_only']:.3f}"
                  f" → 복합 피크 {r['peak_composite']:.3f}")
    print(f"\n출력 위치: {OUT}")


if __name__ == "__main__":
    main()

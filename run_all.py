"""1차년도 4개 시나리오(A·B·C·E) 복합위험도 해석 실행 스크립트.

사용법
    python run_all.py                    # 전체 시나리오 + GIF
    python run_all.py --only A C         # 일부만
    python run_all.py --no-gif           # 표/그래프만 (빠름)
    python run_all.py --lang ko          # 그림 라벨 한글
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Windows 기본 콘솔(cp949)에서도 한글/기호가 깨지지 않도록 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from src import plots
from src.kcoef import link_card
from src.model import Scenario, solve, solve_direct_only

ROOT = Path(__file__).resolve().parent
SCENARIO_DIR = ROOT / "scenarios"
OUT_DIR = ROOT / "outputs"

ORDER = ["A", "B", "C", "E", "F", "G"]
FILES = {
    "A": "scenario_A.json",
    "B": "scenario_B.json",
    "C": "scenario_C.json",
    "E": "scenario_E.json",
    "F": "scenario_F_validation.json",
    "G": "scenario_G_gangnam2022.json",
}


def write_timeseries(scenario: Scenario, result, path: Path):
    keys = [f.key for f in scenario.facilities]
    fields = ["time_hour"]
    for k in keys:
        fields += [f"{k}_direct_risk", f"{k}_interdependency_index", f"{k}_composite_risk"]
    for L in scenario.links:
        fields += [f"G_{L.key}", f"K_{L.key}_per_hour", f"cumtransfer_{L.key}"]
    for k in keys:
        fields.append(f"{k}_cap_adjustment")

    prec = 3 if scenario.t_end <= 3 else (1 if scenario.t_end <= 12 else 1)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for j, t in enumerate(result.times):
            row = {"time_hour": f"{t:.{prec}f}"}
            for i, k in enumerate(keys):
                row[f"{k}_direct_risk"] = f"{result.direct[i, j]:.6f}"
                row[f"{k}_interdependency_index"] = f"{result.interdependency[i, j]:.6f}"
                row[f"{k}_composite_risk"] = f"{result.composite[i, j]:.6f}"
                row[f"{k}_cap_adjustment"] = f"{result.cap_adjustment[i, j]:.6f}"
            for L in scenario.links:
                row[f"G_{L.key}"] = int(result.link_G[L.key][j])
                row[f"K_{L.key}_per_hour"] = f"{L.K:.6f}"
                row[f"cumtransfer_{L.key}"] = f"{result.link_cum[L.key][j]:.6f}"
            w.writerow(row)


def write_link_cards(scenario: Scenario, path: Path):
    cards = [link_card(L, scenario.t_end) for L in scenario.spec["links"]]
    name = {f["key"]: f["name_ko"] for f in scenario.spec["facilities"]}
    for c in cards:
        c["source"] = name.get(c["source"], c["source"])
        c["target"] = name.get(c["target"], c["target"])
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(cards[0].keys()))
        w.writeheader()
        w.writerows(cards)
    return cards


def write_facility_summary(scenario: Scenario, result, path: Path):
    rows = []
    for i, f in enumerate(scenario.facilities):
        direct = result.direct[i, -1]
        inter = result.interdependency[i, -1]
        comp = result.composite[i, -1]
        capped = result.cap_adjustment[i, -1]
        rows.append({
            "scenario": scenario.id,
            "facility_key": f.key,
            "facility_ko": f.name_ko,
            "facility_en": f.name_en,
            "node_kind": f.node_kind,
            "directly_exposed": int(f.directly_exposed),
            "direct_risk_final": round(float(direct), 6),
            "interdependency_index_final": round(float(inter), 6),
            "composite_risk_final": round(float(comp), 6),
            "amplification_ratio": round(float(comp / direct), 4) if direct > 1e-9 else "",
            "capped_amount": round(float(-capped), 6),
            "peak_composite": round(float(result.composite[i].max()), 6),
            "time_of_peak_hour": round(float(result.times[int(result.composite[i].argmax())]), 4),
        })
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def write_link_diagnostics(scenario: Scenario, result, path: Path):
    """링크별 실제 발현 여부. G가 한 번도 1이 되지 않은 링크를 찾아낸다."""
    rows = []
    for L in scenario.links:
        G = result.link_G[L.key]
        post = G[result.times >= L.activation_time_hour]
        frac = float(post.mean()) if len(post) else 0.0
        rows.append({
            "scenario": scenario.id,
            "link_key": L.key,
            "source": scenario.facilities[L.source_idx].name_ko,
            "target": scenario.facilities[L.target_idx].name_ko,
            "K_per_hour": round(L.K, 6),
            "activation_time_hour": L.activation_time_hour,
            "G_on_fraction_after_activation": round(frac, 4),
            "cumulative_transfer": round(float(result.link_cum[L.key][-1]), 6),
            "dormant": int(frac == 0.0),
            "dormant_reason_ko": (
                "활성화 이후에도 원천 위험도가 대상 위험도를 넘지 않아 diffusive 전달항이 0"
                if frac == 0.0 else ""
            ),
            "mechanism_ko": L.mechanism_ko,
        })
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return rows


def validate(scenario: Scenario, result) -> str | None:
    spec = scenario.spec.get("validation")
    if not spec:
        return None
    tol = float(spec.get("tolerance", 1e-3))
    lines = []
    ok = True
    for i, f in enumerate(scenario.facilities):
        if f.key not in spec["expected_final_composite"]:
            continue
        expected = float(spec["expected_final_composite"][f.key])
        got = float(result.composite[i, -1])
        good = abs(got - expected) <= tol
        ok &= good
        lines.append(f"    {'OK ' if good else 'FAIL'} {f.name_ko:<12s} "
                     f"expected {expected:.3f}  got {got:.6f}")
    head = "  [검증] 260918 발표 결과 재현: " + ("일치" if ok else "불일치")
    return head + "\n" + "\n".join(lines)


def run_one(tag: str, *, make_gif: bool, lang: str) -> dict:
    spec = json.loads((SCENARIO_DIR / FILES[tag]).read_text(encoding="utf-8"))
    scenario = Scenario(spec)
    out = OUT_DIR / f"scenario_{tag}"
    out.mkdir(parents=True, exist_ok=True)

    result = solve(scenario)
    direct_only = solve_direct_only(scenario)

    write_timeseries(scenario, result, out / f"{tag}_composite_timeseries.csv")
    cards = write_link_cards(scenario, out / f"{tag}_link_cards.csv")
    rows = write_facility_summary(scenario, result, out / f"{tag}_facility_summary.csv")
    diag = write_link_diagnostics(scenario, result, out / f"{tag}_link_diagnostics.csv")

    plots.composition_bar(scenario, result, out / f"{tag}_composition.png", lang)
    plots.network_diagram(scenario, out / f"{tag}_network.png", lang)
    if make_gif:
        plots.timeline_gif(scenario, direct_only, out / f"{tag}_direct_timeline.gif",
                           with_interdependency=False, lang=lang)
        plots.timeline_gif(scenario, result, out / f"{tag}_composite_timeline.gif",
                           with_interdependency=True, lang=lang)

    print(f"\n=== {scenario.id} — {scenario.title_ko} ===")
    print(f"  해석시간 {scenario.t_start:g}–{scenario.t_end:g} h, "
          f"dt={scenario.dt:g} h, 시설물 {scenario.n}개, 링크 {len(scenario.links)}개")
    print(f"  {'시설물':<18s} {'direct':>8s} {'I':>8s} {'composite':>10s} {'증폭배율':>9s}")
    for r in rows:
        amp = r["amplification_ratio"]
        amp_s = f"{amp:>8.2f}x" if amp != "" else "        -"
        print(f"  {r['facility_ko']:<18s} {r['direct_risk_final']:>8.3f} "
              f"{r['interdependency_index_final']:>8.3f} "
              f"{r['composite_risk_final']:>10.3f} {amp_s}")
    dormant = [d for d in diag if d["dormant"]]
    if dormant:
        print("  [주의] 활성화 시점 이후에도 한 번도 발현하지 않은 링크 "
              f"{len(dormant)}/{len(diag)}개 (diffusive 형식의 구조적 한계):")
        for d in dormant:
            print(f"    - {d['source']} → {d['target']} (K={d['K_per_hour']:.3f})")

    msg = validate(scenario, result)
    if msg:
        print(msg)

    return {"tag": tag, "scenario": scenario, "result": result,
            "rows": rows, "cards": cards, "diag": diag}


def write_cross_summary(runs: list[dict], lang: str):
    all_rows = [r for run in runs for r in run["rows"]]
    path = OUT_DIR / "summary_all_scenarios.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    all_cards = []
    for run in runs:
        for c in run["cards"]:
            all_cards.append({"scenario": run["scenario"].id, **c})
    path = OUT_DIR / "summary_all_link_cards.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_cards[0].keys()))
        w.writeheader()
        w.writerows(all_cards)

    all_diag = [d for run in runs for d in run["diag"]]
    path = OUT_DIR / "summary_all_link_diagnostics.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_diag[0].keys()))
        w.writeheader()
        w.writerows(all_diag)

    # 시나리오별로 상호의존성지수가 가장 큰 시설물
    top = []
    for run in runs:
        best = max(run["rows"], key=lambda r: r["interdependency_index_final"])
        top.append({
            "scenario": run["scenario"].id,
            "facility": best["facility_en"],
            "direct": best["direct_risk_final"],
            "interdependency": best["interdependency_index_final"],
            "composite": best["composite_risk_final"],
        })
    plots.cross_scenario_bar(top, OUT_DIR / "summary_amplification.png", lang)

    print("\n=== 시나리오 간 비교 — 상호의존영향이 가장 큰 시설물 ===")
    print(f"  {'시나리오':<12s} {'시설물':<28s} {'direct':>8s} {'I':>8s} {'composite':>10s}")
    for t in top:
        print(f"  {t['scenario']:<12s} {t['facility']:<28s} {t['direct']:>8.3f} "
              f"{t['interdependency']:>8.3f} {t['composite']:>10.3f}")
    return top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, metavar="TAG",
                    help="실행할 시나리오 (A B C E F)")
    ap.add_argument("--no-gif", action="store_true", help="GIF 생성 생략")
    ap.add_argument("--lang", choices=["en", "ko"], default="en",
                    help="그림 라벨 언어 (기본 en, 260918 발표자료와 동일)")
    args = ap.parse_args()

    if args.lang == "ko":
        plots.use_korean_font()

    tags = [t.upper() for t in (args.only or ORDER)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    runs = [run_one(t, make_gif=not args.no_gif, lang=args.lang) for t in tags]
    if len(runs) > 1:
        write_cross_summary(runs, args.lang)
    print(f"\n출력 위치: {OUT_DIR}")


if __name__ == "__main__":
    main()

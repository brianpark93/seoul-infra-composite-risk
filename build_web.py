"""정적 웹(GitHub Pages)용 데이터 번들 생성.

브라우저에서 해석엔진을 그대로 돌릴 수 있도록 다음을 내보낸다.

    docs/data/scenarios.json     시나리오 메타 + 시설물 + 링크(K 확정치) + 이벤트
    docs/data/direct_<tag>.json  직접위험도 R_direct(t) 를 균일격자로 미리 계산한 값
    docs/data/osm_<key>.json     OSM 기저도 (좌표 반올림·폴리라인 간략화)

왜 직접위험도를 미리 계산해 보내나
    R_direct 는 H·E·V 로만 결정되고 K 와 무관하므로, 사용자가 K 를 바꿔도 변하지 않는다.
    PCHIP 보간을 JS 로 다시 구현해 scipy 와 어긋날 위험을 없애려고, Python 에서
    균일격자로 뽑아 보내고 브라우저는 선형보간만 한다(격자가 촘촘해 오차 무시 가능).

브라우저가 하는 일
    링크 전달항 적분(Explicit Euler)만 JS 로 수행한다. Python 과 같은 dt·같은 스텝수를
    쓰므로 결과가 일치한다.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

from src.model import Scenario

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "docs" / "data"
OSM = ROOT / "data" / "osm"

FILES = {
    "A": "scenario_A.json", "B": "scenario_B.json", "C": "scenario_C.json",
    "E": "scenario_E.json", "F": "scenario_F_validation.json",
    "G": "scenario_G_gangnam2022.json",
}
ORDER = ["G", "A", "B", "C", "E", "F"]      # 실데이터 시나리오를 먼저 보여준다
N_DIRECT = 1400                              # 직접위험도 격자 점 수


def r(v, nd=4):
    return round(float(v), nd)


def simplify(pts, tol=1.2):
    """거리 기반 간략화. 0.1 m 단위 반올림 전에 점 수를 줄인다."""
    if len(pts) <= 2:
        return pts
    out = [pts[0]]
    for p in pts[1:-1]:
        q = out[-1]
        if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 >= tol * tol:
            out.append(p)
    out.append(pts[-1])
    return out


def poly_area(pts):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def export_osm(key):
    src = OSM / f"{key}.json"
    d = json.loads(src.read_text(encoding="utf-8"))
    out = {
        "site": d["site"],
        "source": d["source"],
        "roads": [], "rails": [], "buildings": [], "water": [],
    }
    for road in d["roads"]:
        pts = simplify(road["pts"])
        out["roads"].append({
            "p": [[r(x, 1), r(y, 1)] for x, y in pts],
            "c": road["cls"],
            "n": road.get("name", "") or "",
            "t": 1 if road.get("tunnel") else 0,
        })
    for rail in d["rails"]:
        out["rails"].append({"p": [[r(x, 1), r(y, 1)]
                                   for x, y in simplify(rail["pts"], 2.0)]})
    for b in d["buildings"]:
        pts = simplify(b, 1.6)
        if len(pts) >= 4 and poly_area(pts) >= 350:
            out["buildings"].append([[r(x, 1), r(y, 1)] for x, y in pts])
    for w in d["water"]:
        out["water"].append({"p": [[r(x, 1), r(y, 1)]
                                   for x, y in simplify(w["pts"], 2.0)],
                             "closed": 1 if w.get("closed") else 0})
    dst = WEB / f"osm_{key}.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    return dst, len(out["roads"]), len(out["buildings"])


def export_scenario(tag):
    spec = json.loads((ROOT / "scenarios" / FILES[tag]).read_text(encoding="utf-8"))
    sc = Scenario(spec)

    # 직접위험도 균일격자
    ts = [sc.t_start + (sc.t_end - sc.t_start) * i / (N_DIRECT - 1)
          for i in range(N_DIRECT)]
    direct = [[r(f.profile(t), 5) for t in ts] for f in sc.facilities]
    (WEB / f"direct_{tag}.json").write_text(
        json.dumps({"t0": sc.t_start, "t1": sc.t_end, "n": N_DIRECT,
                    "v": direct}, separators=(",", ":")), encoding="utf-8")

    fac = []
    for i, f in enumerate(sc.facilities):
        src = spec["facilities"][i]
        fac.append({
            "key": f.key, "name": f.name_ko, "short": f.short_ko,
            "color": f.color, "kind": f.node_kind,
            "exposed": 1 if f.directly_exposed else 0,
            "xy": [r(f.map_xy[0], 1), r(f.map_xy[1], 1)],
            "depth": f.depth_m,
            "note": src.get("note_ko", ""),
            "ev": src.get("ev_basis_ko", ""),
            "E": src.get("E_exposure"), "V": src.get("V_vulnerability"),
        })

    links = []
    for i, L in enumerate(sc.links):
        src = spec["links"][i]
        links.append({
            "key": L.key, "s": L.source_idx, "t": L.target_idx,
            "K": r(L.K, 5), "act": L.activation_time_hour,
            "mech": src.get("mechanism_ko", ""),
            "cond": src.get("activation_condition_ko", ""),
            "evid": src.get("evidence_ko", ""),
            "level": src.get("evidence_level", ""),
            "D": src.get("D"), "C": src.get("C"), "B": src.get("B"),
            "tau": src.get("tau_hour"),
        })

    return {
        "tag": tag, "id": sc.id, "title": sc.title_ko,
        "subtitle": spec.get("subtitle_ko", ""),
        "trigger": spec.get("trigger_ko", ""),
        "timescale": spec.get("timescale_note_ko", ""),
        "source": spec.get("source_ko", ""),
        "site": {
            "name": sc.site.get("name_ko", ""),
            "origin": sc.site.get("origin_ko", ""),
            "osm": sc.site.get("osm_key"),
            "trigger_facility": sc.site.get("trigger_facility"),
        },
        "analysis": {
            "t0": sc.t_start, "t1": sc.t_end, "dt": sc.dt,
            "rmax": sc.risk_max, "form": sc.transfer_form,
        },
        "clock0": 16.0 if tag == "G" else None,   # G 는 16:00 이 t=0
        "facilities": fac, "links": links,
        "events": [{"t": e["time_hour"], "label": e["label_ko"],
                    "color": e.get("color", "#475569")} for e in sc.events],
        "observed": spec.get("observed_validation", {}).get("items", []),
    }


def main():
    WEB.mkdir(parents=True, exist_ok=True)
    scenarios = []
    print(f"  {'시나리오':<12s} {'시설물':>6s} {'링크':>5s} {'기저도':>10s}")
    for tag in ORDER:
        s = export_scenario(tag)
        scenarios.append(s)
        print(f"  {s['id']:<12s} {len(s['facilities']):>6d} {len(s['links']):>5d} "
              f"{s['site']['osm'] or '-':>10s}")

    keys = sorted({s["site"]["osm"] for s in scenarios if s["site"]["osm"]})
    print()
    for k in keys:
        dst, nr, nb = export_osm(k)
        print(f"  기저도 {k:<10s} 도로 {nr:4d} · 건물 {nb:4d}  "
              f"{dst.stat().st_size/1024:7.1f} KB")

    (WEB / "scenarios.json").write_text(
        json.dumps({"order": ORDER, "scenarios": scenarios},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    total = sum(p.stat().st_size for p in WEB.glob("*.json"))
    print(f"\n  총 {len(list(WEB.glob('*.json')))}개 파일 · {total/1024/1024:.2f} MB")
    print(f"  출력: {WEB}")


if __name__ == "__main__":
    main()

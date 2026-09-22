"""실데이터 기반 시나리오 G 생성 — 강남역 테스트베드, 2022-08-08 호우 사상.

입력  : data/gangnam2022_rainfall.csv   (관측 앵커 + 재구성 시계열)
출력  : scenarios/scenario_G_gangnam2022.json

자료 출처와 확실성 등급은 data/gangnam2022_sources.md 참조.

직접위험도 산정
    H(t) = 0.5 * I(t)/I_max_obs  +  0.5 * C(t)/C_total
        I(t)  : 시간강우강도 [mm/h]
        I_max : 관측 최대 시간강우 116 mm/h (강남구, 2022-08-08 21:34-22:34)
        C(t)  : 누적강우 [mm],  C_total : 해석창 총강우
      → 8월 설명자료의 Hazard 3축(발생가능성·강도·지속시간) 중
        이미 발생한 사상이므로 발생가능성=1 로 두고, 강도축과 지속시간축을 반영.

    R_direct_i(t) = H(t) * E_i * V_i

    V 는 **직접 노출분만** 담는다. 타 시설물에서 오는 몫은 링크(K)가 담당한다
    (공통원인 / 상호의존 분리 원칙, 8월 설명자료 slide 16).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent
RAIN = ROOT / "data" / "gangnam2022_rainfall.csv"
OUT = ROOT / "scenarios" / "scenario_G_gangnam2022.json"

I_MAX_OBS = 116.0   # [관측] 강남구 최대 1시간 강우, 2022-08-08 21:34-22:34


# --------------------------------------------------------------------------
# 시설물 정의 — E·V 와 그 근거
# --------------------------------------------------------------------------
FACILITIES = [
    dict(
        key="banpo", name_ko="반포천 상류부", name_en="Upper Banpocheon",
        short_ko="반포천 상류", short_en="Banpocheon",
        color="#2563EB", node_kind="medium", directly_exposed=True,
        E=0.70, V=0.55, map_xy=[-551, 9], depth_m=0.0,   # OSM waterway 스냅
        ev_basis_ko=(
            "E: 강남역 배수분구의 최종 방류처. "
            "V: 서울시 공식 침수원인 ③ '반포천 상류부 통수능력 부족'. "
            "다만 반포천 유역분리터널(2021.6 완공, 약 350억)이 2022 사상 시점에 "
            "이미 가동 중이어서 V 를 0.55 로 낮춰 잡았다."),
        note_ko="서울시 공식 침수원인 ③. 유역 최하류 방류 제약.",
    ),
    dict(
        key="sewer_reverse", name_ko="강남대로 하수관로 (역경사 구간)",
        name_en="Gangnam-daero sewer (reverse-slope)",
        short_ko="역경사 하수관로", short_en="Sewer (rev.)",
        color="#D62728", node_kind="facility", directly_exposed=True,
        E=0.85, V=0.95, map_xy=[-70, 240], depth_m=-4.0,
        ev_basis_ko=(
            "E: 강남역 배수분구 전체 우수를 담당. "
            "V: 서울시 공식 침수원인 ②④. 삼성사옥–강남역 지하보도 시공 시 "
            "하수관로 하류측이 약 1.8 m 높게 역경사로 시공되어 배수가 구조적으로 "
            "막혀 있다. 취약성을 최고 수준으로 설정."),
        note_ko="서울시 공식 침수원인 ②④. 하류측 1.8 m 역경사.",
    ),
    dict(
        key="detention", name_ko="용허리 빗물저류조",
        name_en="Yongheori detention basin",
        short_ko="용허리 저류조", short_en="Detention",
        color="#0891B2", node_kind="facility", directly_exposed=True,
        E=0.55, V=0.60, map_xy=[-390, 150], depth_m=-12.0,
        ev_basis_ko=(
            "E: 저지대 아파트 일대 우수 처리 담당. "
            "V: 저류용량 한계. 서울시 긴급대책에 '유입관로 추가 신설'이 포함된 "
            "것은 2022 시점 처리범위가 부족했음을 뜻한다."),
        note_ko="서울시 긴급대책 대상 시설. 위험도 = 저류기능 소진 정도.",
    ),
    dict(
        key="road", name_ko="강남대로 저지대 도로 (EL 12.2 m)",
        name_en="Gangnam-daero low-lying road",
        short_ko="강남대로 저지대", short_en="Road (low)",
        color="#F59E0B", node_kind="facility", directly_exposed=True,
        E=0.95, V=0.45, map_xy=[0, 40], depth_m=0.0,
        ev_basis_ko=(
            "E: 서울 최대 교통량 간선도로 + 보행량. "
            "V: 서울시 공식 침수원인 ① '항아리 지형'. 강남역 일대 해발 12.2 m 로 "
            "인근 논현동·역삼동보다 17 m 이상 낮은 깔때기형 저지대. "
            "다만 V 에는 **지형에 의한 직접 저류분만** 담고, 역경사 관로 역류 기여는 "
            "링크 sewer_reverse→road 로 분리했다."),
        note_ko="서울시 공식 침수원인 ①. 해발 12.2 m, 주변 대비 -17 m.",
    ),
    dict(
        key="station", name_ko="강남역 (2호선·신분당선) 역사",
        name_en="Gangnam Station",
        short_ko="강남역 역사", short_en="Gangnam Stn",
        color="#7C3AED", node_kind="facility", directly_exposed=True,
        E=1.00, V=0.12, map_xy=[59, -159], depth_m=-18.0,  # OSM railway=station 스냅
        ev_basis_ko=(
            "E: 서울 지하철 최다 이용역으로 노출 인구 최대 → 1.00. "
            "V: 지하 역사라 강우에 **직접** 노출되는 경로는 환기구 유입뿐이므로 "
            "0.12 로 낮게 설정. 실제 위험의 대부분은 road→station 전이로 들어온다."),
        note_ko="직접 노출 최소. 위험 대부분이 도로 침수 전이에서 발생.",
    ),
    dict(
        key="manhole", name_ko="보도·맨홀 (효성해링턴타워 일대)",
        name_en="Sidewalk and manholes",
        short_ko="보도·맨홀", short_en="Manholes",
        color="#16A34A", node_kind="facility", directly_exposed=True,
        E=0.60, V=0.15, map_xy=[-180, -70], depth_m=0.0,
        ev_basis_ko=(
            "E: 보행 노출. "
            "V: 강우 직접 노출분은 작다(0.15). 실제 위험은 관로 압력 상승에 따른 "
            "맨홀 역류·뚜껑 이탈에서 오며 이는 sewer_reverse→manhole 링크가 담당한다. "
            "2022-08-08 22:49 경 이 일대에서 맨홀 인명사고가 발생했다."),
        note_ko="2022-08-08 22:49 맨홀 인명사고 발생 지점 일대.",
    ),
]

# --------------------------------------------------------------------------
# 링크 — 서울시 공식 원인분석에 대응
# --------------------------------------------------------------------------
LINKS = [
    dict(key="banpo_to_sewer", source="banpo", target="sewer_reverse",
         dependency_type="물리적 전달 (배수 제약)",
         mechanism_ko="반포천 상류부 통수능력 부족 → 하수관로 방류 불가(배수위 상승) → 관내 수위 상승",
         activation_time_hour=4.0,
         activation_condition_ko="하천 수위 상승으로 관로 방류구 잠김 (8/8 20:00 전후)",
         D=0.60, C=0.75, B=0.35, tau_hour=0.8,
         evidence_ko="서울시 공식 침수원인 ③. 반포천 유역분리터널(2021.6 완공)이 "
                     "남부터미널 일대 유량을 분산해 B=0.35 로 상쇄분을 인정.",
         evidence_level="① 실제 장애·복구자료"),
    dict(key="sewer_to_road", source="sewer_reverse", target="road",
         dependency_type="물리적 전달",
         mechanism_ko="역경사 구간(하류측 +1.8 m)에서 흐름이 막혀 맨홀·빗물받이로 지표 역류 → 도로 침수심 급증",
         activation_time_hour=4.0,
         activation_condition_ko="관로 만관 및 역경사 구간 정체 (8/8 20:00 전후 도로 침수 개시)",
         D=0.80, C=0.90, B=0.10, tau_hour=0.4,
         evidence_ko="서울시 공식 침수원인 ②④. 통상적 관로 월류가 아니라 시공오류로 "
                     "배수가 구조적으로 막힌 상태이므로 C 를 0.90 으로, 대체배수가 "
                     "사실상 없어 B 를 0.10 으로 설정. 본 시나리오 최대 K.",
         evidence_level="① 실제 장애·복구자료"),
    dict(key="detention_to_road", source="detention", target="road",
         dependency_type="서비스 공급 (저류기능 상실)",
         mechanism_ko="저류조 만수로 저지대 우수 수용 불가 → 도로 저류 가중",
         activation_time_hour=5.0,
         activation_condition_ko="저류조 유효용량 소진",
         D=0.35, C=0.70, B=0.30, tau_hour=0.6,
         evidence_ko="서울시 긴급대책에 '용허리 저류조 유입관로 추가 신설'이 포함된 점에서 "
                     "2022 시점 처리범위 부족을 반영.",
         evidence_level="③ 구조화된 전문가판단"),
    dict(key="road_to_station", source="road", target="station",
         dependency_type="물리적 전달",
         mechanism_ko="도로 침수수가 역사 출입구·환기구를 통해 역사·선로로 유입",
         activation_time_hour=5.0,
         activation_condition_ko="도로 침수심이 출입구 방수턱을 초과",
         D=0.90, C=0.80, B=0.40, tau_hour=0.5,
         evidence_ko="역사 침수 경로가 사실상 전량 지표 유입이므로 D=0.90. "
                     "물막이판·집수정·배수펌프가 40% 상쇄.",
         evidence_level="① 실제 장애·복구자료"),
    dict(key="road_to_manhole", source="road", target="manhole",
         dependency_type="물리적 전달",
         mechanism_ko="도로 침수로 보도·맨홀 위치 식별 불가 → 추락 위험 증가",
         activation_time_hour=5.5,
         activation_condition_ko="보도까지 침수 확산",
         D=0.50, C=0.75, B=0.25, tau_hour=0.4,
         evidence_ko="침수 시 맨홀 뚜껑 위치가 보이지 않아 보행 추락 위험이 커진다.",
         evidence_level="③ 구조화된 전문가판단"),
    dict(key="sewer_to_manhole", source="sewer_reverse", target="manhole",
         dependency_type="물리적 전달",
         mechanism_ko="역경사 구간 관내 압력 상승 → 맨홀 뚜껑 이탈·역류 분출",
         activation_time_hour=6.0,
         activation_condition_ko="관내 압력수두가 뚜껑 자중을 초과 (8/8 22:49 인명사고)",
         D=0.75, C=0.85, B=0.10, tau_hour=0.3,
         evidence_ko="2022-08-08 22:49 경 강남 효성해링턴타워 인근 맨홀 사고. "
                     "뚜껑 이탈은 잠금장치가 없으면 상쇄수단이 거의 없어 B=0.10.",
         evidence_level="① 실제 장애·복구자료"),
]

EVENTS = [
    dict(time_hour=4.0, label_ko="20:00 도로 침수 개시",
         label_en="20:00 road flooding starts", color="#475569", label_y=0.78),
    dict(time_hour=5.0, label_ko="21:00 역사 유입 링크 ON",
         label_en="21:00 station inflow ON", color="#7C3AED", label_y=0.88),
    dict(time_hour=6.0, label_ko="22:00 최대 시간강우 116 mm (관측)",
         label_en="22:00 peak 116 mm/h (observed)", color="#DC2626", label_y=0.95),
    dict(time_hour=6.82, label_ko="22:49 맨홀 인명사고 (관측)",
         label_en="22:49 manhole casualty (observed)", color="#B45309", label_y=0.88),
]

BASEMAP = [
    dict(name_ko="강남대로", name_en="Gangnam-daero", kind="road", width=7.5,
         pts=[[0, -520], [0, 620]]),
    dict(name_ko="테헤란로", name_en="Teheran-ro", kind="road", width=6.0,
         pts=[[0, 20], [720, 0]]),
    dict(name_ko="서초대로", name_en="Seocho-daero", kind="road", width=6.0,
         pts=[[-760, 45], [0, 20]]),
    dict(name_ko="2호선", name_en="Line 2", kind="rail", width=2.6,
         pts=[[-760, -25], [720, -45]]),
    dict(name_ko="반포천", name_en="Banpocheon", kind="slope", width=4.5,
         pts=[[-760, -300], [-560, -190], [-330, -230], [-120, -330]]),
]


def load_rain():
    rows = list(csv.DictReader(RAIN.open(encoding="utf-8-sig")))
    t = [float(r["t_hour"]) for r in rows]
    i = [float(r["rain_mm_per_h"]) for r in rows]
    return rows, t, i


def hazard(t, intensity):
    """H(t) = 0.5*강도축 + 0.5*지속시간축."""
    total = 0.0
    cum = []
    for k, v in enumerate(intensity):
        if k > 0:
            total += (intensity[k] + intensity[k - 1]) / 2 * (t[k] - t[k - 1])
        cum.append(total)
    c_total = cum[-1]
    return [0.5 * (v / I_MAX_OBS) + 0.5 * (c / c_total)
            for v, c in zip(intensity, cum)], cum, c_total


def main():
    rows, t, intensity = load_rain()
    H, cum, c_total = hazard(t, intensity)

    print(f"강우 자료  : {RAIN.name}")
    print(f"해석창 누적: {c_total:.1f} mm  (서초구 24h 관측 354.5 mm 와 정합)")
    print(f"최대 강우  : {max(intensity):.0f} mm/h  (관측 앵커 {I_MAX_OBS:.0f} mm/h)")
    print(f"\n  {'시각':>6s} {'t[h]':>5s} {'I[mm/h]':>8s} {'누적[mm]':>9s} {'H(t)':>6s}")
    for r, tt, ii, cc, hh in zip(rows, t, intensity, cum, H):
        print(f"  {r['clock']:>6s} {tt:>5.1f} {ii:>8.0f} {cc:>9.1f} {hh:>6.3f}"
              f"   {r['evidence']}")

    facilities = []
    print(f"\n  {'시설물':<22s} {'E':>5s} {'V':>5s} {'R_direct 최대':>13s}")
    for f in FACILITIES:
        values = [round(h * f["E"] * f["V"], 6) for h in H]
        print(f"  {f['name_ko']:<22s} {f['E']:>5.2f} {f['V']:>5.2f} {max(values):>13.3f}")
        facilities.append({
            "key": f["key"], "name_ko": f["name_ko"], "name_en": f["name_en"],
            "short_ko": f["short_ko"], "short_en": f["short_en"],
            "color": f["color"], "node_kind": f["node_kind"],
            "directly_exposed": f["directly_exposed"],
            "note_ko": f["note_ko"],
            "E_exposure": f["E"], "V_vulnerability": f["V"],
            "ev_basis_ko": f["ev_basis_ko"],
            "map_xy": f["map_xy"], "depth_m": f["depth_m"],
            "layout": [0, 0],
            "direct_risk": {"interpolation": "pchip",
                            "time_hour": t, "value": values},
        })

    spec = {
        "id": "Scenario G",
        "title_ko": "강남역 2022-08-08 호우 (실데이터 기반)",
        "title_en": "Gangnam Station, 8 Aug 2022 storm (data-driven)",
        "subtitle_ko": "기상재해 → 침수·범람 → 설비·동력 상실 (실제 사상)",
        "trigger_ko": "2022-08-08 집중호우 (강남구 최대 116 mm/h)",
        "source_ko": "자료 출처·확실성 등급은 data/gangnam2022_sources.md 참조",
        "timescale_note_ko": (
            "해석창 2022-08-08 16:00 – 08-09 02:00 (10시간). t=0 이 16:00. "
            "관측 최대 시간강우는 t=6.0 (22:00 계급), 맨홀 인명사고는 t=6.82 (22:49)."),
        "hazard_definition_ko": (
            "H(t) = 0.5·I(t)/116 + 0.5·누적(t)/누적총량. "
            "이미 발생한 사상이므로 발생가능성축은 1 로 두고 강도축·지속시간축만 반영."),
        "analysis": {"start_hour": 0.0, "end_hour": 10.0, "dt_hour": 0.002,
                     "save_interval_hour": 0.1, "risk_min": 0.0, "risk_max": 1.0,
                     "transfer_form": "diffusive"},
        "site": {
            "name_ko": "서울 강남역 배수분구 (서초구 서초동·강남구 역삼동)",
            "name_en": "Gangnam Station drainage district, Seoul",
            "origin_ko": "기준점: 강남역 사거리 · 강남역 일대 표고 해발 12.2 m "
                         "(인근 대비 -17 m, 서울시 공식분석)",
            "trigger_facility": "sewer_reverse",
            "view": "plan",
            "osm_key": "gangnam",
            "basemap_source": "© OpenStreetMap contributors (ODbL), Overpass API 경유",
            "axis_note_ko": "평면도(2D). x = 동서, y = 남북 [m], 강남역 사거리 상대좌표. "
                            "시설물 위치는 공식분석의 공간관계를 반영한 개념 배치.",
            "basemap": BASEMAP,
        },
        "facilities": facilities,
        "links": LINKS,
        "events": EVENTS,
        "observed_validation": {
            "note_ko": "모형 결과를 대조할 관측 사실",
            "items": [
                {"t_hour": 4.0, "clock": "20:00",
                 "fact_ko": "강남 일대 도로 차량 다수 침수 시작", "target": "road"},
                {"t_hour": 6.0, "clock": "22:00",
                 "fact_ko": "강남구 최대 시간강우 116 mm (21:34-22:34)", "target": None},
                {"t_hour": 6.82, "clock": "22:49",
                 "fact_ko": "효성해링턴타워 인근 맨홀 인명사고", "target": "manhole"},
            ],
        },
    }

    OUT.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"\n생성: {OUT}")


if __name__ == "__main__":
    main()

"""테스트베드 실제 기저도를 OpenStreetMap(Overpass API)에서 받아온다.

Overpass API 는 인증키가 없다. 받은 지오메트리를 기준점 기준 **로컬 미터 좌표**로
변환해 `data/osm/<site>.json` 에 캐시한다. 이미 캐시가 있으면 다시 받지 않는다.

받는 것
    도로  highway = motorway / trunk / primary / secondary / tertiary / residential
    철도  railway = subway / rail  (지하 노선 포함)
    건물  building = *            (면적 상위만)
    수계  natural=water / waterway = river,stream
    지점  railway=station, subway_entrance

좌표 변환 (기준점 근방 국소 평면 근사)
    x = (lon - lon0) * 111320 * cos(lat0)      [m, 동쪽 +]
    y = (lat - lat0) * 110540                  [m, 북쪽 +]

사용법
    python fetch_osm_basemap.py            # 캐시 없는 것만
    python fetch_osm_basemap.py --force    # 전부 다시
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "osm"
ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
]
UA = "KAIST-AAML-riskmap-research/1.0 (academic use)"

# 테스트베드 기준점 (대략적 위치) 과 반경
SITES = {
    "gangnam": dict(name_ko="강남역 사거리", lat=37.49790, lon=127.02760, radius_m=780),
    "samseong": dict(name_ko="삼성역·영동대로", lat=37.50880, lon=127.06310, radius_m=640),
    "seosomun": dict(name_ko="서소문 고가차도", lat=37.56150, lon=126.97200, radius_m=560),
    "namsan": dict(name_ko="남산1호터널", lat=37.55250, lon=126.98800, radius_m=900),
    "umyeon": dict(name_ko="우면산 북측 남부순환로", lat=37.47600, lon=127.00900, radius_m=820),
}

ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary",
                "residential", "unclassified")


def bbox(lat, lon, r):
    dlat = r / 110540.0
    dlon = r / (111320.0 * math.cos(math.radians(lat)))
    return (lat - dlat, lon - dlon, lat + dlat, lon + dlon)


def query(lat, lon, r):
    s, w, n, e = bbox(lat, lon, r)
    b = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    cls = "|".join(ROAD_CLASSES)
    return f"""[out:json][timeout:90];
(
  way["highway"~"^({cls})$"]({b});
  way["railway"~"^(subway|rail|light_rail)$"]({b});
  way["building"]({b});
  way["natural"="water"]({b});
  way["waterway"~"^(river|stream|canal)$"]({b});
  node["railway"="station"]({b});
);
out geom;"""


def fetch(site_key, spec):
    """공용 Overpass 서버는 자주 504/429 를 낸다. 미러를 돌아가며 재시도한다."""
    q = query(spec["lat"], spec["lon"], spec["radius_m"])
    out = CACHE / f"_raw_{site_key}.json"
    last = None
    for attempt in range(6):
        ep = ENDPOINTS[attempt % len(ENDPOINTS)]
        cmd = ["curl", "-s", "-m", "180", "-A", UA,
               "-H", "Accept: application/json",
               "-X", "POST", ep, "--data-urlencode", f"data={q}",
               "-o", str(out), "-w", "%{http_code}"]
        code = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
        last = code
        if code == "200" and out.exists() and out.stat().st_size > 200:
            try:
                return json.loads(out.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        wait = 6 * (attempt + 1)
        print(f"\n    응답 {code} — {wait}s 후 재시도 ({attempt + 1}/6)", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"Overpass 응답 {last} (site={site_key}) — 모든 미러 실패")


def to_local(lat, lon, lat0, lon0):
    k = 111320.0 * math.cos(math.radians(lat0))
    return [(lon - lon0) * k, (lat - lat0) * 110540.0]


def poly_area(pts):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def convert(raw, spec):
    lat0, lon0 = spec["lat"], spec["lon"]
    roads, rails, buildings, water, stations = [], [], [], [], []
    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            if tags.get("railway") == "station":
                stations.append(dict(name=tags.get("name", ""),
                                     xy=to_local(el["lat"], el["lon"], lat0, lon0)))
            continue
        geom = el.get("geometry") or []
        if len(geom) < 2:
            continue
        pts = [to_local(g["lat"], g["lon"], lat0, lon0) for g in geom]
        if "building" in tags:
            if len(pts) >= 4 and poly_area(pts) >= 350:      # 소형 건물 제외
                buildings.append(pts)
        elif tags.get("natural") == "water" or "waterway" in tags:
            water.append(dict(pts=pts, closed=tags.get("natural") == "water"))
        elif "railway" in tags:
            rails.append(dict(pts=pts, kind=tags["railway"],
                              name=tags.get("name", ""),
                              tunnel=tags.get("tunnel") in ("yes", "building_passage")))
        elif "highway" in tags:
            roads.append(dict(pts=pts, cls=tags["highway"],
                              name=tags.get("name", ""),
                              tunnel=tags.get("tunnel") in ("yes", "building_passage"),
                              bridge=tags.get("bridge") == "yes"))
    return dict(
        site=spec["name_ko"], lat0=lat0, lon0=lon0, radius_m=spec["radius_m"],
        source="OpenStreetMap contributors (ODbL), via Overpass API",
        fetched=time.strftime("%Y-%m-%d %H:%M:%S"),
        roads=roads, rails=rails, buildings=buildings, water=water,
        stations=stations,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    CACHE.mkdir(parents=True, exist_ok=True)
    keys = args.only or list(SITES)
    for k in keys:
        spec = SITES[k]
        dst = CACHE / f"{k}.json"
        if dst.exists() and not args.force:
            d = json.loads(dst.read_text(encoding="utf-8"))
            print(f"  [캐시] {k:<10s} {spec['name_ko']:<18s} "
                  f"도로 {len(d['roads']):4d} · 철도 {len(d['rails']):3d} · "
                  f"건물 {len(d['buildings']):4d} · 수계 {len(d['water']):3d}")
            continue
        print(f"  [수신] {k:<10s} {spec['name_ko']} ...", end="", flush=True)
        raw = fetch(k, spec)
        d = convert(raw, spec)
        dst.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        (CACHE / f"_raw_{k}.json").unlink(missing_ok=True)
        print(f"\r  [수신] {k:<10s} {spec['name_ko']:<18s} "
              f"도로 {len(d['roads']):4d} · 철도 {len(d['rails']):3d} · "
              f"건물 {len(d['buildings']):4d} · 수계 {len(d['water']):3d}")
        time.sleep(2)   # Overpass 공용 서버 예의

    print(f"\n캐시: {CACHE}")
    print("출처: © OpenStreetMap contributors, ODbL. Overpass API 경유.")


if __name__ == "__main__":
    main()

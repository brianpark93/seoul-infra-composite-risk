# 1차년도 4개 시나리오 복합위험도 해석 패키지

성균관대 × KAIST 「복합재난 피해예측·대응 AI 시뮬레이터」 구성기술 1.
260918 개별미팅에서 예시 시나리오 1건에 적용했던 Heat-transfer 기반 복합위험도
방법론을, **1차년도 대상 시나리오 A·B·C·E 전체**에 동일하게 적용하고,
**강남역 2022-08-08 실제 호우 사상(시나리오 G)으로 검증**한다.

결과 해설과 발견사항은 [REPORT.md](REPORT.md) 참조.
브라우저에서 K 를 바꿔가며 즉시 재계산하는 인터랙티브 지도는 [docs/](docs/README.md).
실데이터 출처와 확실성 등급은 [data/gangnam2022_sources.md](data/gangnam2022_sources.md).

---

## 1. 근거 자료

| 자료 | 이 패키지에서의 역할 |
|---|---|
| `성대 개별미팅-260918.pptx` | **기준 방법론.** slide 11–12 지배방정식, slide 13–17 적용방식 |
| `재난안전AI_샘플시나리오_도식_v4_1.pptx` | 시나리오 A(slide 2)·B(3)·C(4)·E(5) 노드·엣지 원본 |
| `260917.../gif/` (make_complex_risk.py) | 해석엔진 검증 기준값 (시나리오 F) |
| `상호의존성지수_HeatEquation_설명자료.pptx` (26.08.28) | K 산정식(slide 18–19), 전달항 3개 옵션(slide 14), 연결정의 원칙(slide 15–17) |
| `회의록.hwpx` (26.09.18) | 해석시간 설정 근거, K 산정 우선순위 |

제안서(`KAIST 1차년도 연구계획서`)의 단순 가중합
`위험도_B = BaseRisk_B + Σ(w_BA × BaseRisk_A)` 대신, **260918 발표의 확산형 미분방정식**을
기준으로 삼았다.

---

## 2. 방법론

### 2.1 지배방정식 (260918 slide 11–12)

```
dR_i/dt = Σ_j G_ij(t) · K_ij · [ R_j(t) − R_i(t) ] + Q_i(t)
```

구현식:

```
Transfer_ji(t)  = G_ji(t) · K_ji · max( R_comp_j − R_comp_i , 0 )
R_comp_i(t+dt)  = clip[ R_comp_i + ΔR_direct_i + Σ_j Transfer_ji · dt , 0, 1 ]
I_i(t)          = max( R_comp_i(t) − R_direct_i(t) , 0 )

R_direct_i(t)   = H_i(t) × E_i(t) × V_i(t)
G_ji(t)         = 1  iff  t ≥ t_activation  ∧  R_j > R_i  ∧  R_i < 1
```

적분은 Explicit Euler. `I_i`(상호의존성지수)는 복합위험도에서 직접위험도를 뺀 잔차다.

### 2.2 K 산정 — 근거를 남기는 방식

260918 발표에서 K는 값만 제시되고 산정근거가 남아 있지 않았다(0.483 / 0.282 / …).
회의에서 심성한 교수님이 **"G, K를 어떻게 산정할 것인가"** 를 직접 질문한 항목이다.

이 패키지는 8월 설명자료 slide 18의 운영식으로 K를 **유도**한다.

```
K_ji(T) = D_ji · C_ji · (1 − B_ji) · H_ji(T)
H_ji(T) = 1 − exp(−T / τ_ji)
```

| 인자 | 의미 | 범위 |
|---|---|---|
| `D` 의존도 | 대상 기능 중 출발 시설물에 의존하는 비중 | 0~1 |
| `C` 전달민감도 | 출발 시설물 고장이 대상 기능 저하로 전환되는 정도 | 0~1 |
| `B` 대체·백업 | 우회공급·예비설비가 실제로 상쇄하는 비중 | 0~1 |
| `τ` 발현시간 | 영향이 나타나기까지의 시정수 [h] | >0 |
| `T` 분석시간 | 해당 시나리오의 해석시간 [h] | — |

각 링크는 D·C·B·τ와 함께 **근거자료·근거수준**(① 실제 장애·복구자료 → ④ 문헌·기본값)을
기록하며, `outputs/*/X_link_cards.csv` 로 출력된다. 홍교수님의 "전문가평가는 재난연구에서
최후의 수단" 원칙에 맞춰 근거수준을 명시적으로 남긴다.

검증: 설명자료 slide 19 예시(전력→펌프, D=1.00, C=0.90, B=0.40, τ=0.5h, T=6h)를
그대로 넣으면 K = 0.54 가 재현된다.

### 2.3 시나리오 도식 → 시설물 네트워크 변환 규칙

원본 도식은 **사건 연쇄 그래프**([T]트리거/[S]전조/[D]물리손상/[F]기능상실/[C]사회파급)이고,
KAIST 방법론은 **시설물 단위**로 위험도를 계산한다. 다음 규칙으로 변환했다.

1. **같은 시설물에 속한 [D]→[F]→[C] 연쇄는 하나의 노드로 묶는다.**
   예) `[D] 지하철 역사 침수 → [F] 운행중단 → [C] 승객 고립` = 시설물 "지하철 역사·선로" 1개.

2. **관리·설계 상태를 나타내는 [S] 노드는 네트워크 노드가 아니라 V(취약성) 인자로 넣는다.**
   예) 시나리오 C의 `정밀점검 지적 미조치`, 시나리오 E의 `가연성 방음판(PMMA)`.
   → 해당 시설물의 t=0 기본위험도를 높이는 방식으로 반영.

3. **공통원인과 상호의존을 분리한다** (8월 설명자료 slide 16).
   같은 재난 H에 여러 시설이 동시에 노출되는 것은 각 시설의 **직접위험도**에 넣고,
   K에는 넣지 않는다. K에 들어가는 것은 `j의 고장이 i의 상태를 직접 바꾸는` 경우뿐이다.
   예) 시나리오 A에서 매설 상수관은 침수에 직접 노출되지 않으며(`directly_exposed: false`),
   `도로 함몰 → 상수관 파손` 경로로만 위험이 전달된다.

4. **연결은 [출발 시설물 + 고장상태] → [도착 시설물 + 핵심기능]** 단위로 정의한다
   (slide 15). 각 링크의 `mechanism_ko` 필드에 기록.

5. **시설물이 아닌 노드는 종류를 구분해 표시한다.**
   `node_kind`: `facility`(시설물) / `medium`(지반 등 매개) / `function`(구조·구급 대응 등 기능).

### 2.4 해석시간

회의록의 *"1–2시간 예측이 목표. 1시간 이내 종료면 갱신이 무의미하고, 6시간 이상
지속되면 갱신이 중요하다"* 는 판단에 따라, 시나리오별 실제 사상 지속시간을 따랐다.

| 시나리오 | 해석시간 T | dt | 근거 |
|---|---:|---:|---|
| A 집중호우 침수 | 8 h | 0.001 h | 호우 사상 1회. 260918 예시와 동일 축척 |
| B 굴착 지반침하 | 72 h | 0.005 h | 전조 [S] 노드 7개. 실제로는 수주~수개월을 3일로 축약 |
| C 교량 붕괴 | 6 h | 0.001 h | 붕괴 → 가스 누출 축적 → 폭발 |
| E 터널 화재 | 2 h | 0.0005 h | 2022.12 제2경인고속도로 방음터널 화재 |

T는 `H(T) = 1 − exp(−T/τ)` 를 통해 K에 직접 들어가므로, 해석시간 변경은 K를 바꾼다.

---

## 3. 서울 테스트베드와 2D 위험도 지도

8월 개념문서 6·9·10절이 최종 산출물로 제시한 **"시설물 위험도 지도 + 시설 간
의존·전이 네트워크"** 를 실제 지점 위에 구현했다. 시나리오마다 서울 시내
테스트베드를 하나씩 두고, **평면도(plan view)** 위에서 위험도가 시간에 따라
변하는 과정을 보여준다.

| 시나리오 | 테스트베드 | 기준점 |
|---|---|---|
| **G 강남역 2022-08-08 호우 (실데이터)** | **강남역 배수분구 (서초구 서초동·강남구 역삼동)** | **강남역 사거리** |
| A 집중호우 침수 | 강남역 일대 (서초구 서초동·강남구 역삼동) | 강남역 사거리 |
| B 굴착 지반침하 | 영동대로 지하공간 복합개발 굴착현장 (강남구 삼성동) | 삼성역 교차로 |
| C 교량 붕괴 | 서소문 고가차도 일대 (중구 서소문동) | 서소문 고가차도 |
| E 터널 화재 | 남산1호터널 (중구) | 터널 중앙부 |
| F 집중호우–토석류 | 우면산 북측 남부순환로 일대 (서초구) | 남부순환로 |

**기저도 — 실제 OpenStreetMap 지오메트리.** `fetch_osm_basemap.py` 가 Overpass API
(인증키 불필요)로 각 테스트베드의 **도로·철도·건물·수계**를 받아 기준점 기준 로컬
미터로 변환해 `data/osm/<key>.json` 에 캐시한다. 렌더러는 도로를 등급별
casing+fill 로, 건물을 면으로, 수계를 하천으로 그린다. 캐시가 없으면 시나리오 JSON
안의 약식 기저도로 자동 대체된다.

```bash
python fetch_osm_basemap.py          # 캐시 없는 것만 수신
python fetch_osm_basemap.py --force  # 전부 다시
```

받은 것 (5개 테스트베드 합계 **도로 1,099 · 철도 98 · 건물 1,240 · 수계 15**):

| 캐시 | 테스트베드 | 도로 | 철도 | 건물 | 수계 |
|---|---|---:|---:|---:|---:|
| `gangnam` | 강남역 사거리 (A·G) | 307 | 15 | 436 | 2 |
| `samseong` | 삼성역·영동대로 (B) | 131 | 8 | 232 | 1 |
| `seosomun` | 서소문 고가차도 (C) | 200 | 63 | 227 | 2 |
| `namsan` | 남산1호터널 (E) | 337 | 8 | 207 | 10 |
| `umyeon` | 우면산 북측 남부순환로 (F) | 124 | 4 | 138 | 0 |

**출처 표기 의무** — © OpenStreetMap contributors, ODbL. 모든 지도 하단에 표기된다.

**시설물 좌표 스냅** — 가능한 노드는 OSM 실제 지형지물에 스냅했다.
예: 강남역 역사 → `railway=station` 노드 (59, −159), 반포천 상류 → 실제 `waterway`
폴리라인 위 (−551, 9). 나머지는 여전히 개념 배치다(`coord_basis_ko` 필드 참조).

**위험도 연속면** — 시설물은 점이지만 위험은 면으로 읽어야 한다. 노드 위험도를
역거리가중(IDW)으로 보간해 반투명 적색 면으로 깐다.

```
w_i(p) = 1 / (d_i(p)² + r0²)     r0 = 70 m
R(p)   = Σ wᵢRᵢ / Σ wᵢ
α(p)   = clip(R(p)/0.85, 0, 1) · 0.62 · fade(최근접거리)
```

`fade` 는 노드에서 멀어질수록 면을 흐리게 해 데이터 없는 곳까지 색칠하는 과잉해석을
막는다. **이 면은 침수심 분포가 아니라 노드 위험도의 공간 보간이다.**

**좌표계** — `x = 동서 [m]`, `y = 남북 [m]`, 기준점 상대좌표, 축척 1:1(equal aspect).
표고축은 쓰지 않는다. 지하 심도는 **노드 테두리(점선 = 지하)** 와 `GL-18 m` 주기로 표시한다.

> 시설물 위치는 공개된 지점을 기준으로 한 **개념 배치**이며 실제 시설물 대장
> 좌표가 아니다. 기저도의 도로선도 개략 형상이다. 실제 적용 시 인하대가 구축할
> 시설물·망 DB의 좌표로 교체하면 코드 수정 없이 그대로 돌아간다.

**색 설계** — 8월 개념문서 9절의 **녹색 → 노랑 → 주황 → 빨강** 관행을 따른다.

이 램프는 **명도 단조 감소가 구조적으로 불가능**하다. 가운데 노랑이 양 끝보다 밝기
때문이다. 색각 이상에서 명도만으로 순서를 읽을 수 없다는 뜻이다. dataviz 검증기
실측(deutan):

| 쌍 | ΔE | 판정 |
|---|---:|---|
| 안전 `#1a9850` vs 심각 `#a50026` | **13.0** | 양호 |
| 안전 `#1a9850` vs 중간 적색 `#d73027` | 5.1 | 미흡 |

그래서 램프 상단을 **진한 적색 `#a50026`** 으로 끝내 안전–심각 구분을 확보했다.
다만 **중간값끼리의 혼동은 남는다.** 색에만 의존하지 않도록 ① 모든 노드에 수치를
직접 표기하고 ② 우측 순위 패널에 수치를 병기하며 ③ 컬러바에 등급명을 글자로 넣었다.

> 이전 버전은 단일 색상 적색 램프(명도 단조 감소, 색각 안전)를 썼다.
> 현장 관행과 8월 문서 표기를 따르기 위해 녹–적 램프로 교체했고,
> 위의 트레이드오프를 감수한 결정이다.

**노드 표현** — 바깥 고리 = 직접위험도, 안쪽 원 = 복합위험도.
두 색이 벌어진 정도가 곧 상호의존성지수 `I` 다.

---

## 4. 실행

```bash
pip install -r requirements.txt

# (1) 시계열 해석 — 260918 발표 그림 계열
python run_all.py                 # 전체 (A·B·C·E + 검증용 F), GIF 포함
python run_all.py --no-gif        # 표·정지그래프만 (빠름)
python run_all.py --only A C      # 일부만
python run_all.py --lang ko       # 그림 라벨 한글 (맑은 고딕)

# (2) 2D 위험도 지도 + 공간 전파 분석
python run_maps.py                # 지도 GIF 2종 + 스냅숏 + 전파분석
python run_maps.py --no-gif       # 스냅숏 PNG 와 전파분석만
python run_maps.py --threshold 0.5

# (3) 전달항 형태 민감도 (diffusive vs saturating)
python compare_transfer_forms.py

# (0) 기저도 수신 (최초 1회)
python fetch_osm_basemap.py

# (5) 인터랙티브 웹 (GitHub Pages) 데이터 빌드 → 배포는 docs/README.md
python build_web.py
cd docs && python -m http.server 8765     # http://127.0.0.1:8765

# (4) 실데이터 시나리오 G — 강남역 2022-08-08 호우
python build_gangnam2022.py        # data/ 의 실측자료 → scenarios/scenario_G_*.json
python validate_gangnam2022.py     # 모형 vs 관측 사건 검증
```

Windows 콘솔에서 한글이 깨지면 `PYTHONIOENCODING=utf-8` 을 붙인다.
지도는 한글 라벨(`--lang ko`)이 기본이다.

---

## 5. 파일 구조

```
├── fetch_osm_basemap.py           OSM 기저도 수신 (Overpass, 인증키 불필요)
├── build_web.py                   정적 웹용 데이터 번들 생성
├── docs/                          **인터랙티브 지도 (GitHub Pages 배포용)**
│   ├── index.html · style.css · app.js
│   ├── data/*.json                시나리오·직접위험도·OSM 기저도 (0.67 MB)
│   └── README.md                  배포 방법과 배포 전 확인사항
├── build_gangnam2022.py           실데이터 → 시나리오 G 생성
├── validate_gangnam2022.py        시나리오 G 검증 (모형 vs 관측)
├── data/
│   ├── osm/<key>.json             OSM 기저도 캐시 (도로·철도·건물·수계)
│   ├── gangnam2022_rainfall.csv   2022-08-08 강우 (관측 앵커 + 재구성)
│   └── gangnam2022_sources.md     **자료 출처와 확실성 등급**
├── run_all.py                     시계열 해석 실행
├── run_maps.py                    2D 위험도 지도 + 공간 전파 분석
├── compare_transfer_forms.py      전달항 형태 민감도 분석
├── requirements.txt
├── README.md                      (이 문서) 방법론·사용법
├── REPORT.md                      결과 해설 및 발견사항
├── src/
│   ├── kcoef.py                   K = D·C·(1−B)·H(T) 산정
│   ├── model.py                   해석엔진 (Euler 적분, G 판정)
│   ├── plots.py                   GIF · 구성막대 · 네트워크도
│   ├── basemap.py                 OSM 기저도 렌더링 · 위험도 IDW 연속면 · 글로우
│   └── riskmap.py                 2D 평면 위험도 지도 · 공간 전파분석
├── scenarios/
│   ├── scenario_A.json            강남역 집중호우 침수 연쇄
│   ├── scenario_B.json            노후 하수관·굴착 지반침하 연쇄
│   ├── scenario_C.json            대형 시설물 붕괴 연쇄
│   ├── scenario_E.json            터널 화재 연쇄
│   ├── scenario_F_validation.json 260918 발표 재현 (엔진 검증)
│   └── scenario_G_gangnam2022.json 강남역 2022-08-08 호우 (실데이터, 자동생성)
└── outputs/
    ├── scenario_X/
    │   ├── X_direct_timeline.gif       상호의존 고려 X  (발표 slide 15 대응)
    │   ├── X_composite_timeline.gif    상호의존 고려 O  (발표 slide 16 대응)
    │   ├── X_composition.png           최종시점 위험도 분해 (slide 17 대응)
    │   ├── X_network.png               G·K 연결관계 도식 (slide 14 대응)
    │   ├── X_composite_timeseries.csv  전체 시계열 (direct/I/composite/G/K/누적전달)
    │   ├── X_facility_summary.csv      시설물별 최종값·증폭배율·피크시점
    │   ├── X_link_cards.csv            링크별 K 산정근거 카드
    │   ├── X_link_diagnostics.csv      링크 발현 여부 진단
    │   ├── X_riskmap.gif               2D 위험도 지도 애니메이션 + 순위 패널
    │   ├── X_riskmap_compare.gif       상호의존 고려 X / O 지도 나란히 비교
    │   ├── X_riskmap_snapshots.png     시점별 지도 6컷 (슬라이드용)
    │   └── X_propagation.csv           임계 도달시간·거리·전파속도
    ├── summary_all_scenarios.csv
    ├── summary_all_link_cards.csv
    ├── summary_all_link_diagnostics.csv
    ├── summary_amplification.png
    ├── scenario_G/G_validation.png / G_validation_*.csv   모형 vs 관측 검증
    ├── summary_propagation.csv / summary_propagation.png
    ├── sensitivity_transfer_form.png / *.csv / sensitivity_log.txt
    ├── run_log.txt / map_log.txt / sensitivity_log.txt
```

---

## 6. 수정 방법

- **직접위험도(H×E×V) 값** → `scenarios/scenario_X.json` 의 `facilities[].direct_risk`
  (`time_hour` / `value` 쌍, `interpolation`은 `linear` 또는 `pchip`)
- **연결관계·K** → `links[]` 의 `D`/`C`/`B`/`tau_hour`/`activation_time_hour`.
  기존 산정치를 그대로 쓰려면 `K_per_hour` 를 직접 적으면 D·C·B·τ 는 무시된다.
- **해석시간·dt·전달항 형태** → `analysis`
- **시설물 추가** → `facilities[]` 에 항목 추가 후 `layout`(네트워크도 좌표)과
  `map_xy`(평면도 [동 m, 북 m]), `depth_m`(심도) 지정
- **테스트베드 위치·기저도** → `site.basemap` (도로/철도/구역 폴리라인)과
  `site.origin_ko`. 실제 GIS 좌표를 쓰려면 기준점 기준 미터 오프셋으로 변환해 넣는다.

---

## 7. 검증

`scenario_F_validation.json` 은 260918 발표에 사용한 입력을 그대로 넣어,
본 엔진이 원본 `make_complex_risk.py` 의 결과를 재현하는지 확인한다.

| 시설물 | 원본 (gif/composite_risk_timeseries.csv) | 본 엔진 | 
|---|---:|---:|
| 하수·배수시설 | 0.850000 | 0.850000 |
| 지하철역사 | 0.770063 | 0.770063 |
| 도로 | 1.000000 | 1.000000 |
| 주택 | 0.994266 | 0.994266 |

허용오차 1e-5 로 일치. `python run_all.py --only F --no-gif` 로 재확인할 수 있다.

> 참고: 원본 README의 5행 표(0/2/4/6/8h)는 사건 시점만 발췌한 것이고,
> 실제 입력은 `direct_facility_risk_timeseries.csv` 의 1시간 간격 9개 점이다.
> 5행만 쓰면 보간이 달라져 지하철역사 0.763, 주택 1.000 이 나온다.

---

## 8. 이 패키지의 값은 예시값이다

직접위험도 곡선과 D·C·B·τ 는 **시나리오 도식의 인과구조와 문헌·사례에 근거한 설명용
가정값**이며, 실측 데이터가 아니다. 실제 적용 시에는

- 직접위험도 → 강원대(침수)·건기연(지반)·ETRI(화재) 등 도메인 기관의 H 산정결과와
  시설물 DB의 E·V로 대체
- K → 실제 장애·복구 이력에서 D·C·B·τ 를 역산 (회의록의 *"실제 데이터에 대해서 역으로
  뽑아낼 수도 있음"*)
- 전이확률 P가 별도로 제공되면 `W_ji = G_ji · P_ji · K_ji` 로 결합
  (P = 발현확률, K = 발현했을 때 전달강도. **같은 증거를 P와 K에 중복 반영하지 않을 것**)

로 교체한다. 구조는 그대로 두고 숫자만 바꾸면 되도록 JSON을 분리해 두었다.

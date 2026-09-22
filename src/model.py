"""Heat-transfer 기반 복합위험도 해석 엔진.

지배방정식 (성대 개별미팅-260918.pptx, slide 11-12)

    dR_i/dt = sum_j G_ij(t) * K_ij * [ R_j(t) - R_i(t) ] + Q_i(t)

구현식 (260917 gif 패키지와 동일)

    Transfer_ji(t) = G_ji(t) * K_ji * max(R_comp_j - R_comp_i, 0)
    R_comp_i(t+dt) = clip[ R_comp_i + dR_direct_i + sum_j Transfer_ji * dt , 0, 1 ]
    I_i(t)         = max( R_comp_i(t) - R_direct_i(t), 0 )

    G_ji(t) = 1  iff  t >= t_activation  and  R_j > R_i  and  R_i < R_max
            = 0  otherwise

여기서
    R_direct_i(t) = H_i(t) * E_i(t) * V_i(t)  (시나리오 CSV/JSON 입력)
    I_i(t)        = 상호의존성지수 (복합위험도 중 직접위험도를 뺀 잔차)

전달항 형태는 두 가지를 지원한다.

    diffusive  (기본) : K * max(R_j - R_i, 0)        -- 260918 발표 반영식
                        G 조건에 R_j > R_i 를 포함한다. 따라서 위험도가 더 낮은
                        시설물로만 전달되며, 되먹임(feedback) 링크는 발현하지 않는다.

    saturating        : K * R_j * (1 - R_i)          -- 설명자료 slide 14 옵션 C
                        R_j > R_i 조건이 없어 되먹임 루프와 '위험 증폭'을 표현할 수
                        있고, (1 - R_i) 항이 상한 1을 자연스럽게 만든다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import PchipInterpolator

from .kcoef import resolve_link_K


# --------------------------------------------------------------------------
# 직접위험도 R_direct(t)
# --------------------------------------------------------------------------
class DirectRiskProfile:
    """시설물별 직접위험도 R_direct(t) = H*E*V 의 시간 프로파일."""

    def __init__(self, times, values, method: str = "linear"):
        self.times = np.asarray(times, dtype=float)
        self.values = np.asarray(values, dtype=float)
        self.method = method
        if method == "pchip":
            self._interp = PchipInterpolator(self.times, self.values)
        elif method == "linear":
            self._interp = None
        else:
            raise ValueError(f"unknown interpolation: {method}")

    def __call__(self, t: float) -> float:
        if self._interp is None:
            return float(np.interp(t, self.times, self.values))
        # PCHIP 은 구간 밖에서 발산하므로 양 끝은 고정한다.
        t_clamped = min(max(t, self.times[0]), self.times[-1])
        return float(self._interp(t_clamped))


@dataclass
class Facility:
    key: str
    name_ko: str
    name_en: str
    color: str
    profile: DirectRiskProfile
    directly_exposed: bool = True
    note_ko: str = ""
    node_kind: str = "facility"  # facility | medium | function
    layout: tuple[float, float] = (0.0, 0.0)     # 네트워크 도식용 추상 좌표
    map_xy: tuple[float, float] = (0.0, 0.0)     # 평면도용 [동쪽 m, 북쪽 m]
    depth_m: float = 0.0                         # 심도 (지표 0, 지하 음수)
    short_ko: str = ""
    short_en: str = ""


@dataclass
class Link:
    key: str
    source_idx: int
    target_idx: int
    K: float
    activation_time_hour: float
    source_name: str = ""
    target_name: str = ""
    mechanism_ko: str = ""
    dependency_type: str = ""
    K_basis: str = ""


@dataclass
class Result:
    times: np.ndarray                  # (nt,)
    direct: np.ndarray                 # (nf, nt)
    interdependency: np.ndarray        # (nf, nt)
    composite: np.ndarray              # (nf, nt)
    link_G: dict = field(default_factory=dict)      # key -> (nt,)
    link_cum: dict = field(default_factory=dict)    # key -> (nt,) 누적 전달량
    cap_adjustment: np.ndarray = None  # (nf, nt) clip 으로 잘려나간 누적량


# --------------------------------------------------------------------------
# 시나리오
# --------------------------------------------------------------------------
class Scenario:
    def __init__(self, spec: dict):
        self.spec = spec
        self.id = spec["id"]
        self.title_ko = spec["title_ko"]
        self.title_en = spec["title_en"]
        self.subtitle_ko = spec.get("subtitle_ko", "")
        self.trigger_ko = spec.get("trigger_ko", "")

        a = spec["analysis"]
        self.t_start = float(a["start_hour"])
        self.t_end = float(a["end_hour"])
        self.dt = float(a["dt_hour"])
        self.save_interval = float(a["save_interval_hour"])
        self.risk_min = float(a.get("risk_min", 0.0))
        self.risk_max = float(a.get("risk_max", 1.0))
        self.transfer_form = a.get("transfer_form", "diffusive")

        self.facilities: list[Facility] = []
        for i, f in enumerate(spec["facilities"]):
            dr = f["direct_risk"]
            self.facilities.append(
                Facility(
                    key=f["key"],
                    name_ko=f["name_ko"],
                    name_en=f["name_en"],
                    color=f["color"],
                    profile=DirectRiskProfile(
                        dr["time_hour"], dr["value"], dr.get("interpolation", "linear")
                    ),
                    directly_exposed=f.get("directly_exposed", True),
                    note_ko=f.get("note_ko", ""),
                    node_kind=f.get("node_kind", "facility"),
                    layout=tuple(f.get("layout", (i * 1.0, 0.0))),
                    map_xy=tuple(f.get("map_xy", (i * 30.0, 0.0))),
                    depth_m=float(f.get("depth_m", 0.0)),
                    short_ko=f.get("short_ko", f["name_ko"]),
                    short_en=f.get("short_en", f["name_en"]),
                )
            )

        idx = {f.key: i for i, f in enumerate(self.facilities)}
        self.links: list[Link] = []
        for L in spec["links"]:
            K, basis = resolve_link_K(L, self.t_end)
            self.links.append(
                Link(
                    key=L["key"],
                    source_idx=idx[L["source"]],
                    target_idx=idx[L["target"]],
                    K=K,
                    activation_time_hour=float(L["activation_time_hour"]),
                    source_name=self.facilities[idx[L["source"]]].name_en,
                    target_name=self.facilities[idx[L["target"]]].name_en,
                    mechanism_ko=L.get("mechanism_ko", ""),
                    dependency_type=L.get("dependency_type", ""),
                    K_basis=basis,
                )
            )

        self.events = spec.get("events", [])
        self.figure = spec.get("figure", {})
        self.site = spec.get("site", {
            "name_ko": "", "name_en": "",
            "trigger_facility": spec["facilities"][0]["key"],
            "axis_note_ko": "",
        })

    @property
    def n(self) -> int:
        return len(self.facilities)

    def direct_risk(self, t: float) -> np.ndarray:
        return np.array([f.profile(t) for f in self.facilities], dtype=float)


# --------------------------------------------------------------------------
# 적분
# --------------------------------------------------------------------------
def link_is_active(scenario: Scenario, link: Link, t: float, R: np.ndarray) -> bool:
    """G_ji(t) 판정.

    공통 조건 : t >= 활성화시간  AND  대상 시설물이 아직 상한에 닿지 않음
    diffusive : 추가로 원천 위험도 > 대상 위험도 (위험은 '내리막'으로만 전달)
    saturating: 추가 조건 없음 (되먹임·증폭 허용)
    """
    if t < link.activation_time_hour:
        return False
    if R[link.target_idx] >= scenario.risk_max:
        return False
    if scenario.transfer_form == "diffusive":
        return bool(R[link.source_idx] > R[link.target_idx])
    return True


def solve(scenario: Scenario) -> Result:
    """Explicit Euler 로 복합위험도 시계열을 계산한다."""
    n = scenario.n
    dt = scenario.dt
    t_end = scenario.t_end
    steps = int(round((t_end - scenario.t_start) / dt))

    R = scenario.direct_risk(scenario.t_start)
    previous_direct = R.copy()
    cap_adjustment = np.zeros(n)
    cum_transfer = {L.key: 0.0 for L in scenario.links}

    save_times = np.round(
        np.arange(scenario.t_start, t_end + scenario.save_interval / 100,
                  scenario.save_interval),
        10,
    )
    rec_t, rec_direct, rec_I, rec_R = [], [], [], []
    rec_G = {L.key: [] for L in scenario.links}
    rec_cum = {L.key: [] for L in scenario.links}
    rec_cap = []

    def record(t: float):
        direct = scenario.direct_risk(t)
        rec_t.append(t)
        rec_direct.append(direct.copy())
        rec_I.append(np.maximum(R - direct, 0.0))
        rec_R.append(R.copy())
        rec_cap.append(cap_adjustment.copy())
        for L in scenario.links:
            rec_G[L.key].append(int(link_is_active(scenario, L, t, R)))
            rec_cum[L.key].append(cum_transfer[L.key])

    record(scenario.t_start)
    save_index = 1

    for step in range(1, steps + 1):
        t = scenario.t_start + step * dt
        current_direct = scenario.direct_risk(t)
        direct_increment = current_direct - previous_direct

        coupling_rate = np.zeros(n)
        for L in scenario.links:
            s, g = L.source_idx, L.target_idx
            if not link_is_active(scenario, L, t, R):
                continue
            if scenario.transfer_form == "saturating":
                flux = L.K * R[s] * (1.0 - R[g])
            else:
                flux = L.K * (R[s] - R[g])
            coupling_rate[g] += flux
            cum_transfer[L.key] += flux * dt

        uncapped = R + direct_increment + coupling_rate * dt
        capped = np.clip(uncapped, scenario.risk_min, scenario.risk_max)
        cap_adjustment += capped - uncapped
        R = capped
        previous_direct = current_direct

        if save_index < len(save_times) and t + dt / 2 >= save_times[save_index]:
            record(float(save_times[save_index]))
            save_index += 1

    return Result(
        times=np.array(rec_t),
        direct=np.array(rec_direct).T,
        interdependency=np.array(rec_I).T,
        composite=np.array(rec_R).T,
        link_G={k: np.array(v) for k, v in rec_G.items()},
        link_cum={k: np.array(v) for k, v in rec_cum.items()},
        cap_adjustment=np.array(rec_cap).T,
    )


def solve_direct_only(scenario: Scenario) -> Result:
    """상호의존영향을 끈 해석(발표자료 slide 15 '고려 X' 대응)."""
    times = np.round(
        np.arange(scenario.t_start,
                  scenario.t_end + scenario.save_interval / 100,
                  scenario.save_interval),
        10,
    )
    direct = np.array([scenario.direct_risk(float(t)) for t in times]).T
    return Result(
        times=times,
        direct=direct,
        interdependency=np.zeros_like(direct),
        composite=direct.copy(),
        cap_adjustment=np.zeros_like(direct),
    )

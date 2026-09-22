"""위험전달계수 K 산정.

산정식 출처: 상호의존성지수_HeatEquation_설명자료.pptx (2026-08-28) slide 18

    K_ji(T) = D_ji * C_ji * (1 - B_ji) * H_ji(T)
    H_ji(T) = 1 - exp(-T / tau_ji)

    D      의존도      : 대상 기능 중 출발 시설물에 의존하는 비중        (0~1)
    C      전달민감도  : 출발 시설물 고장이 대상 기능 저하로 전환되는 정도 (0~1)
    B      대체/백업   : 우회공급/예비설비가 실제로 상쇄하는 비중         (0~1)
    H(T)   시간효과    : 분석시간 T 동안 영향이 발현되는 정도             (0~1)
    tau    발현시간    : 영향이 나타나기까지의 시정수 [h]

검증 예시(설명자료 slide 19, 전력->펌프):
    D=1.00, C=0.90, B=0.40, tau=0.5h, T=6h
    H(6) = 1 - exp(-12) ~= 1.000
    K    = 1.00 * 0.90 * 0.60 * 1.000 = 0.54
"""

from __future__ import annotations

import math


def time_effect(T_hour: float, tau_hour: float) -> float:
    """H(T) = 1 - exp(-T/tau). tau<=0 이면 즉시 발현(1.0)으로 본다."""
    if tau_hour <= 0:
        return 1.0
    return 1.0 - math.exp(-float(T_hour) / float(tau_hour))


def derive_K(D: float, C: float, B: float, tau_hour: float, T_hour: float) -> float:
    """K = D * C * (1-B) * H(T). 단위는 h^-1."""
    for label, value in (("D", D), ("C", C), ("B", B)):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} must be within 0~1, got {value}")
    return D * C * (1.0 - B) * time_effect(T_hour, tau_hour)


def resolve_link_K(link: dict, T_hour: float) -> tuple[float, str]:
    """링크 정의에서 K를 확정한다.

    - ``K_per_hour`` 가 있으면 그 값을 그대로 사용한다(기존 산정치 재현용).
    - 없으면 ``D``/``C``/``B``/``tau_hour`` 로부터 산정한다.

    Returns:
        (K, 산정근거 문자열)
    """
    if "K_per_hour" in link and link["K_per_hour"] is not None:
        return float(link["K_per_hour"]), "given"

    D = float(link["D"])
    C = float(link["C"])
    B = float(link["B"])
    tau = float(link["tau_hour"])
    K = derive_K(D, C, B, tau, T_hour)
    basis = (
        f"K = D({D:.2f}) x C({C:.2f}) x (1-B({B:.2f})) x H(T={T_hour:g}h, tau={tau:g}h)"
        f" = {K:.4f}"
    )
    return K, basis


def link_card(link: dict, T_hour: float) -> dict:
    """링크 1개의 K 산정 근거 카드(설명자료 slide 19 형식)를 만든다."""
    K, basis = resolve_link_K(link, T_hour)
    tau = link.get("tau_hour")
    return {
        "link_key": link["key"],
        "source": link["source"],
        "target": link["target"],
        "mechanism_ko": link.get("mechanism_ko", ""),
        "dependency_type": link.get("dependency_type", ""),
        "D_dependency": link.get("D", ""),
        "C_sensitivity": link.get("C", ""),
        "B_backup": link.get("B", ""),
        "tau_hour": tau if tau is not None else "",
        "T_hour": T_hour,
        "H_T": round(time_effect(T_hour, tau), 6) if tau is not None else "",
        "K_per_hour": round(K, 6),
        "K_basis": basis,
        "activation_time_hour": link["activation_time_hour"],
        "activation_condition_ko": link.get("activation_condition_ko", ""),
        "evidence_ko": link.get("evidence_ko", ""),
        "evidence_level": link.get("evidence_level", ""),
    }

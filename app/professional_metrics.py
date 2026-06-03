from __future__ import annotations

import math
from typing import Any

from .indicators import clamp


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _score_band(value: float, low: float, high: float, max_score: float, soft: float = 0.0) -> float:
    """İdeal bant içindeyse tam puan, banttan uzaklaştıkça yumuşak düşüş."""
    if low <= value <= high:
        return max_score
    if soft <= 0:
        return 0.0
    if value < low:
        return clamp(max_score * (1 - (low - value) / soft), 0, max_score)
    return clamp(max_score * (1 - (value - high) / soft), 0, max_score)


def compute_pre_pump_metrics(feature: Any, previous: dict[str, Any] | None) -> dict[str, Any]:
    """
    professional_paper_v4: Pump Detective bulgularına göre daha açıklanabilir skor.

    İlk dump analizinden çıkan ana fikir:
    1. En iyi v3 işlemler RSI 55-65 ve 24h %5-%30 bandında geldi.
    2. Çok uçmuş 24h > %30 coinlerde FOMO cezası gerekiyor.
    3. PORTAL gibi recovery coinler hacim patlamadan da yükselebiliyor; bu yüzden
       sadece volume_ratio'ya bağımlı kalmayan RECOVERY_COMPRESSION fazı eklendi.
    4. HOME/EPIC profili için momentum genişlemesi ve skor açıklama bileşenleri eklendi.
    """
    previous = previous or {}

    prev_price = _f(previous.get("price"), feature.price)
    prev_volume_ratio = _f(previous.get("volume_ratio"), 1.0)
    prev_score = _f(previous.get("parlayan_score"), feature.parlayan_score)
    prev_extra = previous.get("extra") or {}
    prev_phase = str(prev_extra.get("market_phase") or "UNKNOWN")
    prev_pre_pump_score = _f(prev_extra.get("pre_pump_score"), 0.0)

    price_delta_from_prev_pct = 0.0
    if prev_price > 0:
        price_delta_from_prev_pct = ((float(feature.price) - prev_price) / prev_price) * 100.0

    volume_ratio = _f(feature.volume_ratio, 1.0)
    volume_acceleration = volume_ratio - prev_volume_ratio
    score_delta = _f(feature.parlayan_score) - prev_score

    change_5m = _f(feature.price_change_5m_pct)
    change_15m = _f(feature.price_change_15m_pct)
    change_30m = _f(feature.price_change_30m_pct)
    change_24h = _f(feature.price_change_24h_pct)
    rsi = _f(feature.rsi, 50.0)
    spread_pct = _f(feature.spread_pct, 0.0)
    fake_risk = _f(feature.fake_pump_risk)
    liquidity_score = _f(feature.liquidity_score)
    momentum_score = _f(feature.momentum_score)
    extra = getattr(feature, "extra", {}) or {}
    directional_volume_score = _f(extra.get("directional_volume_score"), 50.0)
    up_volume_ratio = _f(extra.get("up_volume_ratio"), 0.5)
    close_location_score = _f(extra.get("close_location_score"), 0.5)

    # Açıklanabilir bileşenler (0-100'e yakın toplam)
    recovery_compression = 0.0
    if 32 <= rsi <= 48 and -12 <= change_30m <= 1.5 and -6 <= change_15m <= 2.8:
        # PORTAL benzeri: düşük RSI + satış sonrası sıkışma + erken toparlanma
        recovery_compression = 16.0
        recovery_compression += _score_band(change_5m, -1.2, 1.8, 8.0, soft=2.5)
        recovery_compression += _score_band(volume_ratio, 0.35, 1.60, 5.0, soft=1.5)

    volume_component = clamp(math.log(max(volume_ratio, 0.1), 2) * 13.0, -6, 22)
    volume_accel_component = clamp(volume_acceleration * 10.0, -7, 15)

    momentum_component = 0.0
    momentum_component += clamp(change_5m * 4.2, -10, 14)
    momentum_component += clamp(change_15m * 2.4, -10, 18)
    momentum_component += clamp(change_30m * 1.2, -8, 16)

    rsi_component = _score_band(rsi, 55, 65, 16.0, soft=14.0)
    # Recovery sinyali RSI 36-48 civarında geldiğinde cezayı azalt.
    if recovery_compression > 0:
        rsi_component = max(rsi_component, _score_band(rsi, 36, 48, 10.0, soft=10.0))

    position_component = _score_band(change_24h, 4.0, 30.0, 15.0, soft=18.0)
    quality_component = clamp((liquidity_score - fake_risk) / 3.0, -18, 18)
    directional_component = clamp((directional_volume_score - 48.0) * 0.18, -7, 9)
    if up_volume_ratio >= 0.58 and close_location_score >= 0.55:
        directional_component += 3.0
    directional_component = clamp(directional_component, -7, 12)

    score_delta_component = clamp(score_delta * 0.55, -6, 9)
    pre_score_trend_component = clamp((prev_pre_pump_score and (_f(prev_pre_pump_score) * 0.0)) + score_delta_component, -6, 9)

    spread_penalty = clamp(max(spread_pct - 0.22, 0) * 80.0, 0, 22)
    late_penalty = 0.0
    late_penalty += clamp(max(change_24h - 30.0, 0) * 1.8, 0, 40)
    late_penalty += clamp(max(change_15m - 11.0, 0) * 2.2, 0, 28)
    late_penalty += clamp(max(rsi - 72.0, 0) * 2.0, 0, 25)
    late_penalty += clamp(max(fake_risk - 70.0, 0) * 0.8, 0, 18)

    base = 26.0
    pre_pump_score = base
    pre_pump_score += recovery_compression
    pre_pump_score += volume_component + volume_accel_component
    pre_pump_score += momentum_component
    pre_pump_score += rsi_component + position_component + quality_component
    pre_pump_score += pre_score_trend_component
    pre_pump_score -= spread_penalty + late_penalty
    pre_pump_score = round(clamp(pre_pump_score), 2)

    # Faz motoru: coin değil, faz al.
    if fake_risk >= 82 or spread_pct >= 0.55:
        phase = "DANGER"
    elif change_24h >= 35 or change_15m >= 13 or rsi >= 76:
        phase = "FOMO"
    elif recovery_compression >= 22 and pre_pump_score >= 52:
        phase = "RECOVERY_COMPRESSION"
    elif volume_ratio >= 1.8 and -1.0 <= change_5m <= 2.2 and change_24h <= 18:
        phase = "VOLUME_WAKEUP"
    elif pre_pump_score >= 76 and 0.1 <= change_5m <= 4.5 and 1.0 <= change_15m <= 8.5 and change_24h <= 30:
        phase = "ACCUMULATION_BREAKOUT"
    elif pre_pump_score >= 66 and 0.0 <= change_5m <= 5.5 and change_24h <= 32:
        phase = "EARLY_MOMENTUM"
    elif pre_pump_score >= 58 and change_15m > 0 and change_30m > 0 and rsi <= 70:
        phase = "MOMENTUM_EXPANSION"
    elif change_24h >= 22 and momentum_score < 45:
        phase = "DISTRIBUTION"
    else:
        phase = "WATCH"

    components = {
        "base": round(base, 4),
        "recovery_compression": round(recovery_compression, 4),
        "volume_component": round(volume_component, 4),
        "volume_accel_component": round(volume_accel_component, 4),
        "momentum_component": round(momentum_component, 4),
        "rsi_component": round(rsi_component, 4),
        "position_component": round(position_component, 4),
        "quality_component": round(quality_component, 4),
        "score_delta_component": round(pre_score_trend_component, 4),
        "spread_penalty": round(spread_penalty, 4),
        "late_penalty": round(late_penalty, 4),
    }

    return {
        "pre_pump_score": pre_pump_score,
        "market_phase": phase,
        "previous_phase": prev_phase,
        "phase_changed": phase != prev_phase,
        "price_delta_from_prev_pct": round(price_delta_from_prev_pct, 5),
        "volume_acceleration": round(volume_acceleration, 5),
        "score_delta": round(score_delta, 5),
        "early_price_window": bool(-1.0 <= change_5m <= 4.5 and -1.5 <= change_15m <= 9.0),
        "not_late_24h": round(1.0 if change_24h <= 30 else max(0.0, 1.0 - (change_24h - 30) / 25), 4),
        "late_penalty": round(late_penalty, 4),
        "spread_penalty": round(spread_penalty, 4),
        "score_components": components,
        "v4_profile": (
            "RECOVERY_PUMP" if phase == "RECOVERY_COMPRESSION"
            else "EARLY_MOMENTUM" if phase in {"VOLUME_WAKEUP", "EARLY_MOMENTUM", "ACCUMULATION_BREAKOUT", "MOMENTUM_EXPANSION"}
            else "FOMO_OR_DANGER" if phase in {"FOMO", "DANGER"}
            else "WATCH"
        ),
    }


def build_eugene_explanation(feature: Any, metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    phase = metrics.get("market_phase", "WATCH")
    components = metrics.get("score_components") or {}
    reasons.append(f"faz={phase}")
    reasons.append(f"pre_pump_score={_f(metrics.get('pre_pump_score')):.1f}")
    reasons.append(f"profile={metrics.get('v4_profile', 'WATCH')}")
    reasons.append(f"volume_ratio={_f(feature.volume_ratio):.2f}")
    reasons.append(f"volume_accel={_f(metrics.get('volume_acceleration')):.2f}")
    reasons.append(f"5m={_f(feature.price_change_5m_pct):.2f}%")
    reasons.append(f"15m={_f(feature.price_change_15m_pct):.2f}%")
    reasons.append(f"30m={_f(feature.price_change_30m_pct):.2f}%")
    reasons.append(f"24h={_f(feature.price_change_24h_pct):.2f}%")
    reasons.append(f"rsi={_f(feature.rsi):.1f}")
    reasons.append(f"liq={_f(feature.liquidity_score):.1f}")
    reasons.append(f"fake_risk={_f(feature.fake_pump_risk):.1f}")
    if components:
        reasons.append(
            "components="
            + ",".join(f"{k}:{_f(v):.1f}" for k, v in components.items())
        )
    return reasons

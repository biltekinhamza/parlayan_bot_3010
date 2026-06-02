from __future__ import annotations

from typing import Any

from .models import MarketFeature


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


class DecisionEngine:
    """
    professional_paper_v4 karar motoru.

    Canlı emir yoktur. Bu motor sadece paper trade / araştırma amaçlıdır.
    v4 hedefi:
    - v3 kazanan işlemlerde görülen RSI 55-65, 24h %5-%30, skor 45-75 bölgesini korumak
    - FOMO fazına girişleri engellemek
    - PORTAL gibi RECOVERY_COMPRESSION adaylarını izlemek, ancak teyit gelmeden girmemek
    - Her reddi nedenleriyle kaydetmek
    """

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def decide(
        self,
        feature: MarketFeature,
        has_open_trade: bool = False,
        in_cooldown: bool = False,
    ) -> dict[str, Any]:
        cfg = self.config["strategy"]
        parlayan_cfg = cfg["parlayan"]

        reasons: list[str] = []

        if in_cooldown:
            return {"action": "COOLDOWN", "reasons": ["sembol geçici olarak beklemede"], "entry_ok": False}

        if feature.rsi is None:
            return {"action": "REJECT", "reasons": ["RSI verisi yok"], "entry_ok": False}

        rsi = float(feature.rsi)
        change_24h = _f(feature.price_change_24h_pct)
        change_5m = _f(feature.price_change_5m_pct)
        change_15m = _f(feature.price_change_15m_pct)
        change_30m = _f(feature.price_change_30m_pct)
        volume_ratio = _f(feature.volume_ratio, 1.0)
        extra = feature.extra or {}
        pre_pump_score = _f(extra.get("pre_pump_score"), 0.0)
        market_phase = str(extra.get("market_phase") or "WATCH")
        v4_profile = str(extra.get("v4_profile") or "WATCH")

        blocked_entry_phases = {"FOMO", "LATE_FOMO", "DANGER", "DISTRIBUTION"}
        watchable_phases = {
            "RECOVERY_COMPRESSION",
            "VOLUME_WAKEUP",
            "EARLY_MOMENTUM",
            "ACCUMULATION_BREAKOUT",
            "MOMENTUM_EXPANSION",
        }

        if market_phase == "DANGER":
            return {"action": "REJECT", "reasons": ["faz tehlikeli: DANGER"], "entry_ok": False}

        if market_phase in {"FOMO", "LATE_FOMO"} and change_24h >= _f(parlayan_cfg.get("hard_fomo_24h_block_pct"), 35):
            return {
                "action": "FOMO_BLOCK",
                "reasons": [f"geç kalmış FOMO: faz={market_phase}, 24h={change_24h:.2f}%"],
                "entry_ok": False,
            }

        # WATCH filtresi: düşük kaliteli coinleri aday listesine bile alma.
        min_pre_pump_watch = _f(parlayan_cfg.get("min_pre_pump_score_for_watch"), 48.0)
        min_24h = _f(parlayan_cfg.get("min_24h_change_pct"), 3.0)
        min_watch_score = _f(parlayan_cfg.get("min_parlayan_score_for_watch"), 20.0)

        if market_phase not in watchable_phases and change_24h < min_24h and pre_pump_score < min_pre_pump_watch:
            reasons.append(
                f"erken sinyal zayıf: faz={market_phase}, 24h={change_24h:.2f}%, pre_pump={pre_pump_score:.1f}"
            )

        min_liq = _f(parlayan_cfg.get("min_liquidity_score"), 28)
        if feature.liquidity_score < min_liq:
            reasons.append(f"likidite düşük: {feature.liquidity_score:.1f} < {min_liq:.0f}")

        max_spread = _f(parlayan_cfg.get("max_spread_pct"), 0.45)
        if feature.spread_pct is not None and feature.spread_pct > max_spread:
            reasons.append(f"spread yüksek: {feature.spread_pct:.3f}% > {max_spread:.2f}%")

        if feature.parlayan_score < min_watch_score and pre_pump_score < min_pre_pump_watch:
            reasons.append(
                f"skorlar düşük: parlayan={feature.parlayan_score:.1f}, pre_pump={pre_pump_score:.1f}"
            )

        if reasons:
            return {"action": "REJECT", "reasons": reasons, "entry_ok": False}

        # ENTRY profilleri.
        min_pre_pump_entry = _f(parlayan_cfg.get("min_pre_pump_score_for_entry"), 64.0)
        min_score_entry = _f(parlayan_cfg.get("min_parlayan_score_for_entry"), 48.0)
        min_24h_entry = _f(parlayan_cfg.get("min_24h_change_pct_for_entry"), 4.0)
        max_24h_entry = _f(parlayan_cfg.get("max_24h_change_pct_for_entry"), 30.0)
        hard_24h_block = _f(parlayan_cfg.get("hard_fomo_24h_block_pct"), 35.0)
        min_volume_entry = _f(parlayan_cfg.get("min_volume_ratio_for_entry"), 0.75)
        min_5m_entry = _f(parlayan_cfg.get("min_5m_change_for_entry"), 0.0)
        min_15m_entry = _f(parlayan_cfg.get("min_15m_change_for_entry"), 0.25)

        entry_reasons: list[str] = []

        if has_open_trade:
            entry_reasons.append("bu sembolde zaten açık işlem var")

        if market_phase in blocked_entry_phases:
            entry_reasons.append(f"entry fazı yasak: {market_phase}")

        if change_24h > hard_24h_block:
            entry_reasons.append(f"24h çok koşmuş: {change_24h:.2f}% > {hard_24h_block:.1f}%")

        if volume_ratio < min_volume_entry:
            entry_reasons.append(f"entry volume yetersiz: {volume_ratio:.2f} < {min_volume_entry:.2f}")

        if feature.fake_pump_risk > _f(parlayan_cfg.get("max_fake_pump_risk_for_entry"), 72):
            entry_reasons.append(f"fake pump riski yüksek: {feature.fake_pump_risk:.1f}")

        # v4 ideal momentum: HOME/EPIC/PORTAL entry tarafında görülen bölge.
        ideal_rsi_zone = _f(parlayan_cfg.get("ideal_rsi_min"), 52.0) <= rsi <= _f(parlayan_cfg.get("ideal_rsi_max"), 68.0)
        ideal_24h_zone = min_24h_entry <= change_24h <= max_24h_entry
        positive_structure = change_5m >= min_5m_entry and change_15m >= min_15m_entry and change_30m >= _f(parlayan_cfg.get("min_30m_change_for_entry"), -0.5)

        momentum_entry = (
            market_phase in {"EARLY_MOMENTUM", "ACCUMULATION_BREAKOUT", "MOMENTUM_EXPANSION"}
            and pre_pump_score >= min_pre_pump_entry
            and feature.parlayan_score >= min_score_entry
            and ideal_24h_zone
            and ideal_rsi_zone
            and positive_structure
        )

        # Recovery entry: PORTAL tarzı coinleri kaçırmamak için.
        # Ancak direkt düşüş bıçağına girmemek için 5m/15m toparlanma teyidi istiyoruz.
        recovery_entry = (
            market_phase == "RECOVERY_COMPRESSION"
            and pre_pump_score >= _f(parlayan_cfg.get("min_recovery_score_for_entry"), 66.0)
            and 36 <= rsi <= 55
            and change_5m >= _f(parlayan_cfg.get("min_recovery_5m_change_for_entry"), 0.15)
            and change_15m >= _f(parlayan_cfg.get("min_recovery_15m_change_for_entry"), 0.35)
            and change_24h <= _f(parlayan_cfg.get("max_recovery_24h_change_for_entry"), 28.0)
        )

        volume_wakeup_entry = (
            market_phase == "VOLUME_WAKEUP"
            and pre_pump_score >= _f(parlayan_cfg.get("min_volume_wakeup_score_for_entry"), 70.0)
            and 48 <= rsi <= 66
            and change_5m >= 0
            and change_15m >= 0
            and change_24h <= 22
        )

        if not (momentum_entry or recovery_entry or volume_wakeup_entry):
            entry_reasons.append(
                "v4 entry profili oluşmadı: "
                f"faz={market_phase}, profile={v4_profile}, pre={pre_pump_score:.1f}, "
                f"score={feature.parlayan_score:.1f}, rsi={rsi:.1f}, "
                f"5m={change_5m:.2f}, 15m={change_15m:.2f}, 30m={change_30m:.2f}, 24h={change_24h:.2f}"
            )

        if entry_reasons:
            return {
                "action": "PARLAYAN_WATCH",
                "reasons": [f"watch: {r}" for r in entry_reasons],
                "entry_ok": False,
                "parlayan_score": feature.parlayan_score,
                "pre_pump_score": pre_pump_score,
                "market_phase": market_phase,
                "v4_profile": v4_profile,
                "change_24h": change_24h,
            }

        entry_profile = "MOMENTUM_ENTRY" if momentum_entry else "RECOVERY_ENTRY" if recovery_entry else "VOLUME_WAKEUP_ENTRY"
        return {
            "action": "PARLAYAN_ENTRY",
            "reasons": [
                f"entry_profile={entry_profile}",
                f"faz={market_phase}",
                f"profile={v4_profile}",
                f"pre_pump={pre_pump_score:.1f}",
                f"24h={change_24h:.2f}%",
                f"parlayan_score={feature.parlayan_score:.1f}",
                f"volume_ratio={volume_ratio:.2f}",
                f"rsi={rsi:.1f}",
                f"5m={change_5m:.2f}%",
                f"15m={change_15m:.2f}%",
                f"30m={change_30m:.2f}%",
            ],
            "entry_ok": True,
            "entry_profile": entry_profile,
            "parlayan_score": feature.parlayan_score,
            "pre_pump_score": pre_pump_score,
            "market_phase": market_phase,
            "v4_profile": v4_profile,
            "change_24h": change_24h,
        }

# -*- coding: utf-8 -*-
"""Three-layer position sizing service.

Layer 1 — Base position (risk-based): per-trade risk budget divided by
per-unit risk (entry - stop). Caps individual trade exposure.

Layer 2 — Account net worth dynamic adjustment: scale total exposure
based on recent drawdown of the account equity curve. As drawdown
deepens, the multiplier shrinks; as the account recovers, it expands.

Layer 3 — Opportunity classification risk mapping: scale exposure based
on signal strength (decision action / score). Stronger signals get
larger weights; weak signals get smaller weights.

Final position = Layer1(base) × Layer2(net_worth_multiplier) × Layer3(opportunity_multiplier)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.config import Config, get_config


logger = logging.getLogger(__name__)


# ── Opportunity tiers (mapped from DecisionAction / sentiment score) ──
# These map signal strength to a 0-1 weight multiplier.
OPPORTUNITY_TIER_WEIGHTS: Dict[str, float] = {
    "strong_buy": 1.0,
    "buy": 0.8,
    "add": 0.7,
    "hold": 0.4,
    "watch": 0.2,
    "reduce": 0.1,
    "sell": 0.0,
    "alert": 0.15,
    "avoid": 0.0,
}

DEFAULT_OPPORTUNITY_TIER = "watch"


@dataclass(frozen=True)
class PositionSizerConfig:
    """Configuration for three-layer position sizing."""

    # Layer 1: Base position
    risk_per_trade_pct: float = 2.0  # max % of equity to risk per trade
    max_position_pct: float = 25.0  # max % of equity for single position

    # Layer 2: Account net worth dynamic adjustment
    drawdown_scale_thresholds: tuple = (
        # (drawdown_pct_start, drawdown_pct_end, multiplier)
        (0.0, 5.0, 1.0),    # no drawdown → full exposure
        (5.0, 10.0, 0.8),   # mild drawdown → reduce to 80%
        (10.0, 15.0, 0.5),  # moderate drawdown → reduce to 50%
        (15.0, 100.0, 0.2), # severe drawdown → reduce to 20%
    )

    # Layer 3: Opportunity classification risk mapping
    opportunity_tier_weights: Dict[str, float] = field(
        default_factory=lambda: dict(OPPORTUNITY_TIER_WEIGHTS)
    )

    # Global caps
    max_total_exposure_pct: float = 95.0  # max % of equity invested


@dataclass(frozen=True)
class Layer1BasePosition:
    """Layer 1 result: base position sizing."""

    risk_budget_amount: float  # absolute amount at risk
    per_unit_risk: Optional[float]  # entry - stop per share
    suggested_quantity: Optional[int]  # whole shares
    suggested_value: float  # dollar value of position
    position_pct_of_equity: float  # position as % of equity
    capped_by: str  # "risk_budget" | "max_position" | "no_stop_loss"


@dataclass(frozen=True)
class Layer2NetWorthAdjustment:
    """Layer 2 result: net worth dynamic adjustment."""

    current_drawdown_pct: Optional[float]
    multiplier: float
    tier: str  # "normal" | "mild_drawdown" | "moderate_drawdown" | "severe_drawdown"


@dataclass(frozen=True)
class Layer3OpportunityMapping:
    """Layer 3 result: opportunity classification risk mapping."""

    opportunity_tier: str
    weight: float
    source: str  # "decision_action" | "sentiment_score" | "default"


@dataclass(frozen=True)
class PositionSizingResult:
    """Final three-layer position sizing result."""

    layer1_base: Layer1BasePosition
    layer2_net_worth: Layer2NetWorthAdjustment
    layer3_opportunity: Layer3OpportunityMapping

    final_quantity: int
    final_value: float
    final_pct_of_equity: float

    account_equity: float
    entry_price: float
    stop_loss_price: Optional[float]


class PositionSizer:
    """Three-layer position sizing service.

    Usage:
        sizer = PositionSizer(config)
        result = sizer.compute_position_size(
            account_equity=100000.0,
            entry_price=10.0,
            stop_loss_price=9.0,
            opportunity_tier="buy",
            current_drawdown_pct=7.5,
        )
    """

    def __init__(self, config: Optional[PositionSizerConfig] = None):
        self.config = config or PositionSizerConfig()

    # ── Layer 1: Base position (risk-based) ──

    def compute_base_position(
        self,
        *,
        account_equity: float,
        entry_price: float,
        stop_loss_price: Optional[float],
    ) -> Layer1BasePosition:
        """Compute base position size from risk budget.

        Formula: quantity = risk_budget / per_unit_risk
        Capped by max_position_pct of equity.
        """
        if account_equity <= 0 or entry_price <= 0:
            return Layer1BasePosition(
                risk_budget_amount=0.0,
                per_unit_risk=None,
                suggested_quantity=0,
                suggested_value=0.0,
                position_pct_of_equity=0.0,
                capped_by="risk_budget",
            )

        risk_budget = account_equity * self.config.risk_per_trade_pct / 100.0
        max_position_value = account_equity * self.config.max_position_pct / 100.0

        if stop_loss_price is None or stop_loss_price >= entry_price:
            # No valid stop loss → use max position cap
            suggested_value = max_position_value
            suggested_quantity = int(suggested_value / entry_price)
            return Layer1BasePosition(
                risk_budget_amount=risk_budget,
                per_unit_risk=None,
                suggested_quantity=suggested_quantity,
                suggested_value=suggested_quantity * entry_price,
                position_pct_of_equity=(suggested_quantity * entry_price / account_equity) * 100,
                capped_by="no_stop_loss",
            )

        per_unit_risk = entry_price - stop_loss_price
        if per_unit_risk <= 0:
            suggested_quantity = 0
            suggested_value = 0.0
        else:
            risk_based_quantity = int(risk_budget / per_unit_risk)
            max_quantity = int(max_position_value / entry_price)
            suggested_quantity = min(risk_based_quantity, max_quantity)
            suggested_value = suggested_quantity * entry_price

        capped_by = "max_position" if suggested_quantity * entry_price >= max_position_value - 0.01 else "risk_budget"

        return Layer1BasePosition(
            risk_budget_amount=risk_budget,
            per_unit_risk=per_unit_risk,
            suggested_quantity=suggested_quantity,
            suggested_value=suggested_value,
            position_pct_of_equity=(suggested_value / account_equity) * 100 if account_equity > 0 else 0.0,
            capped_by=capped_by,
        )

    # ── Layer 2: Account net worth dynamic adjustment ──

    def compute_net_worth_adjustment(
        self,
        *,
        current_drawdown_pct: Optional[float],
    ) -> Layer2NetWorthAdjustment:
        """Scale exposure based on current account drawdown.

        As drawdown deepens, the multiplier shrinks to preserve capital.
        """
        if current_drawdown_pct is None or current_drawdown_pct < 0:
            return Layer2NetWorthAdjustment(
                current_drawdown_pct=current_drawdown_pct,
                multiplier=1.0,
                tier="normal",
            )

        dd = current_drawdown_pct
        for dd_start, dd_end, multiplier in self.config.drawdown_scale_thresholds:
            if dd_start <= dd < dd_end:
                if dd < 5.0:
                    tier = "normal"
                elif dd < 10.0:
                    tier = "mild_drawdown"
                elif dd < 15.0:
                    tier = "moderate_drawdown"
                else:
                    tier = "severe_drawdown"
                return Layer2NetWorthAdjustment(
                    current_drawdown_pct=dd,
                    multiplier=multiplier,
                    tier=tier,
                )

        # Fallback: severe drawdown
        return Layer2NetWorthAdjustment(
            current_drawdown_pct=dd,
            multiplier=0.2,
            tier="severe_drawdown",
        )

    # ── Layer 3: Opportunity classification risk mapping ──

    def compute_opportunity_mapping(
        self,
        *,
        opportunity_tier: Optional[str] = None,
        sentiment_score: Optional[int] = None,
    ) -> Layer3OpportunityMapping:
        """Map signal strength to position weight.

        Priority: explicit opportunity_tier > sentiment_score mapping > default.
        """
        weights = self.config.opportunity_tier_weights

        if opportunity_tier and opportunity_tier in weights:
            return Layer3OpportunityMapping(
                opportunity_tier=opportunity_tier,
                weight=weights[opportunity_tier],
                source="decision_action",
            )

        if sentiment_score is not None:
            tier = self._tier_from_sentiment_score(sentiment_score)
            return Layer3OpportunityMapping(
                opportunity_tier=tier,
                weight=weights.get(tier, weights[DEFAULT_OPPORTUNITY_TIER]),
                source="sentiment_score",
            )

        return Layer3OpportunityMapping(
            opportunity_tier=DEFAULT_OPPORTUNITY_TIER,
            weight=weights[DEFAULT_OPPORTUNITY_TIER],
            source="default",
        )

    @staticmethod
    def _tier_from_sentiment_score(score: int) -> str:
        """Map a 0-100 sentiment score to an opportunity tier."""
        if score >= 85:
            return "strong_buy"
        if score >= 70:
            return "buy"
        if score >= 55:
            return "add"
        if score >= 40:
            return "hold"
        if score >= 25:
            return "watch"
        if score >= 10:
            return "reduce"
        return "sell"

    # ── Combined three-layer computation ──

    def compute_position_size(
        self,
        *,
        account_equity: float,
        entry_price: float,
        stop_loss_price: Optional[float] = None,
        opportunity_tier: Optional[str] = None,
        sentiment_score: Optional[int] = None,
        current_drawdown_pct: Optional[float] = None,
    ) -> PositionSizingResult:
        """Compute final position size using all three layers.

        Final quantity = Layer1(base_quantity) × Layer2(multiplier) × Layer3(weight)
        """
        layer1 = self.compute_base_position(
            account_equity=account_equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
        )
        layer2 = self.compute_net_worth_adjustment(
            current_drawdown_pct=current_drawdown_pct,
        )
        layer3 = self.compute_opportunity_mapping(
            opportunity_tier=opportunity_tier,
            sentiment_score=sentiment_score,
        )

        combined_multiplier = layer2.multiplier * layer3.weight
        final_quantity = int(layer1.suggested_quantity * combined_multiplier)
        final_value = final_quantity * entry_price
        final_pct = (final_value / account_equity * 100) if account_equity > 0 else 0.0

        # Enforce global cap
        max_total_value = account_equity * self.config.max_total_exposure_pct / 100.0
        if final_value > max_total_value:
            final_quantity = int(max_total_value / entry_price)
            final_value = final_quantity * entry_price
            final_pct = (final_value / account_equity * 100) if account_equity > 0 else 0.0

        return PositionSizingResult(
            layer1_base=layer1,
            layer2_net_worth=layer2,
            layer3_opportunity=layer3,
            final_quantity=final_quantity,
            final_value=final_value,
            final_pct_of_equity=final_pct,
            account_equity=account_equity,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
        )

    def compute_batch(
        self,
        *,
        account_equity: float,
        candidates: Sequence[Dict[str, Any]],
        current_drawdown_pct: Optional[float] = None,
    ) -> List[PositionSizingResult]:
        """Compute position sizes for multiple candidates.

        Each candidate dict should contain:
        - entry_price (required)
        - stop_loss_price (optional)
        - opportunity_tier (optional)
        - sentiment_score (optional)
        """
        layer2 = self.compute_net_worth_adjustment(
            current_drawdown_pct=current_drawdown_pct,
        )
        results: List[PositionSizingResult] = []
        for candidate in candidates:
            entry_price = float(candidate.get("entry_price", 0))
            stop_loss = candidate.get("stop_loss_price")
            stop_loss = float(stop_loss) if stop_loss is not None else None
            tier = candidate.get("opportunity_tier")
            score = candidate.get("sentiment_score")

            result = self.compute_position_size(
                account_equity=account_equity,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
                opportunity_tier=tier,
                sentiment_score=score if isinstance(score, int) else None,
                current_drawdown_pct=current_drawdown_pct,
            )
            results.append(result)
        return results

    # ── Serialization ──

    @staticmethod
    def result_to_dict(result: PositionSizingResult) -> Dict[str, Any]:
        """Convert a PositionSizingResult to a serializable dict."""
        return {
            "layer1_base": {
                "risk_budget_amount": round(result.layer1_base.risk_budget_amount, 2),
                "per_unit_risk": round(result.layer1_base.per_unit_risk, 4) if result.layer1_base.per_unit_risk else None,
                "suggested_quantity": result.layer1_base.suggested_quantity,
                "suggested_value": round(result.layer1_base.suggested_value, 2),
                "position_pct_of_equity": round(result.layer1_base.position_pct_of_equity, 2),
                "capped_by": result.layer1_base.capped_by,
            },
            "layer2_net_worth": {
                "current_drawdown_pct": result.layer2_net_worth.current_drawdown_pct,
                "multiplier": result.layer2_net_worth.multiplier,
                "tier": result.layer2_net_worth.tier,
            },
            "layer3_opportunity": {
                "opportunity_tier": result.layer3_opportunity.opportunity_tier,
                "weight": result.layer3_opportunity.weight,
                "source": result.layer3_opportunity.source,
            },
            "final_quantity": result.final_quantity,
            "final_value": round(result.final_value, 2),
            "final_pct_of_equity": round(result.final_pct_of_equity, 2),
            "account_equity": round(result.account_equity, 2),
            "entry_price": result.entry_price,
            "stop_loss_price": result.stop_loss_price,
        }


def build_position_sizer_from_config(app_config: Optional[Config] = None) -> PositionSizer:
    """Build a PositionSizer using app-level Config overrides."""
    cfg = app_config or get_config()

    risk_per_trade = float(getattr(cfg, "position_sizer_risk_per_trade_pct", 2.0))
    max_position = float(getattr(cfg, "position_sizer_max_position_pct", 25.0))
    max_total = float(getattr(cfg, "position_sizer_max_total_exposure_pct", 95.0))

    return PositionSizer(
        config=PositionSizerConfig(
            risk_per_trade_pct=risk_per_trade,
            max_position_pct=max_position,
            max_total_exposure_pct=max_total,
        )
    )

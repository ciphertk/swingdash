"""The Risk tab's settings: capital, risk per trade and limits, remembered across runs."""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass

from swingdash.adapters.storage.repos.app_state import AppStateRepository
from swingdash.domain.risk.charges import SCHEDULES, Broker, ChargeSchedule
from swingdash.domain.risk.discipline import AssumedStop
from swingdash.domain.risk.sizing import RiskMode, RiskSpec

logger = logging.getLogger(__name__)

_KEY = "risk_settings"


@dataclass(frozen=True)
class RiskSettings:
    capital: float = 0.0  # 0: not set yet
    risk_mode: RiskMode = RiskMode.PERCENT
    risk_percent: float = 1.0
    risk_amount: float = 5_000.0
    max_allocation_pct: float = 20.0
    max_heat_pct: float = 6.0
    atr_period: int = 14
    atr_multiple: float = 1.5
    low_sessions: int = 10
    liquidity_warn_pct: float = 1.0
    broker: Broker = Broker.UPSTOX  # whose delivery charges go into the risk
    # For positions without a usable stop: assume one this far below the price.
    assumed_stop_use_atr: bool = True  # atr_multiple x ATR; else the percentage
    assumed_stop_pct: float = 8.0  # also the fallback without ATR history
    broker_history_days: int = 365  # how far back a first broker sync reads trades

    @property
    def risk(self) -> RiskSpec:
        value = self.risk_percent if self.risk_mode is RiskMode.PERCENT else self.risk_amount
        return RiskSpec(self.risk_mode, value)

    @property
    def charges(self) -> ChargeSchedule:
        return SCHEDULES[self.broker]

    @property
    def assumed_stop(self) -> AssumedStop:
        return AssumedStop(
            percent=self.assumed_stop_pct,
            atr_multiple=self.atr_multiple if self.assumed_stop_use_atr else None,
        )


class RiskSettingsService:
    def __init__(self, state: AppStateRepository) -> None:
        self._state = state
        self._cached: RiskSettings | None = None

    def get(self) -> RiskSettings:
        if self._cached is None:
            self._cached = self._load()
        return self._cached

    def save(self, settings: RiskSettings) -> None:
        data = dataclasses.asdict(settings)
        data["risk_mode"] = settings.risk_mode.value
        data["broker"] = settings.broker.value
        self._state.set(_KEY, json.dumps(data))
        self._cached = settings

    def _load(self) -> RiskSettings:
        raw = self._state.get(_KEY)
        if not raw:
            return RiskSettings()
        try:
            data = json.loads(raw)
            known = {f.name: f for f in dataclasses.fields(RiskSettings)}
            values = {name: value for name, value in data.items() if name in known}
            if "risk_mode" in values:
                values["risk_mode"] = RiskMode(values["risk_mode"])
            if "broker" in values:
                values["broker"] = Broker(values["broker"])
            return RiskSettings(**values)
        except (ValueError, TypeError):
            logger.warning("ignoring unreadable risk settings", exc_info=True)
            return RiskSettings()

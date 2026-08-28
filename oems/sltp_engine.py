"""止损止盈策略库：自动学习历史订单与因子库形态，产出可执行策略。"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backtest_store.repository import SltpCaseRecord


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    if n % 2:
        return float(vals[n // 2])
    return float((vals[n // 2 - 1] + vals[n // 2]) / 2)


def _percentile(values: list[float], pct: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    idx = int(math.ceil(pct / 100.0 * len(vals))) - 1
    idx = min(max(idx, 0), len(vals) - 1)
    return float(vals[idx])


class SltpEngine:
    """自动学习并维护止损止盈策略库，供智能止损引擎实时调用。"""

    def __init__(self, repository: Any, gateway: Any = None, market: Any = None) -> None:
        self.repository = repository
        self.gateway = gateway
        self.market = market
        self.model_version = "v0"
        self.trained_at: str = ""

    def _bars(self, symbol: str, timeframe: str = "M15", limit: int = 500) -> list[dict[str, Any]]:
        try:
            if self.gateway:
                return self.gateway.get_rates(symbol, timeframe, limit) or []
        except Exception:
            pass
        try:
            if self.market:
                return self.market.get_bars(symbol, timeframe, limit=limit) or []
        except Exception:
            pass
        return []

    def _contract(self, symbol: str) -> float:
        try:
            if self.gateway:
                info = self.gateway.get_symbol_info(symbol)
                if info:
                    return float(info.get("contract_size") or 100.0)
        except Exception:
            pass
        return 100.0

    def learn(self) -> dict[str, Any]:
        trades = self.repository.list_trades(limit=1000)
        groups: dict[str, dict[str, Any]] = {}
        for trade in trades:
            if not trade.exit_time or not trade.symbol:
                continue
            side = "long" if trade.side in ("long", "buy") else "short"
            key = f"{trade.symbol}:{side}:{trade.factor_id or 'manual'}"
            group = groups.setdefault(
                key,
                {
                    "symbol": trade.symbol,
                    "side": side,
                    "factor_id": trade.factor_id or "",
                    "trades": [],
                    "entry_prices": [],
                    "pnls": [],
                    "stop_usd": [],
                    "take_usd": [],
                    "best_entry": [],
                    "best_exit": [],
                    "highs": [],
                    "lows": [],
                    "peak_lots": [],
                    "support_levels": [],
                    "resistance_levels": [],
                    "fingerprint": {},
                },
            )
            group["trades"].append(trade)
            group["entry_prices"].append(float(trade.entry_price or 0.0))
            group["pnls"].append(float(trade.pnl or 0.0))
            if trade.peak_pnl is not None:
                lots = float(trade.lots or 0.0)
                if lots > 0:
                    group["peak_lots"].append(float(trade.peak_pnl) / lots)

            bars = self._bars(trade.symbol)
            window = bars
            try:
                entry_dt = trade.entry_time
                exit_dt = trade.exit_time
                if entry_dt and exit_dt:
                    window = [
                        b
                        for b in bars
                        if entry_dt <= pd.to_datetime(b["time"], utc=True).to_pydatetime() <= exit_dt
                    ]
            except Exception:
                window = bars
            if not window:
                window = bars
            if window:
                highs = [float(b["high"]) for b in window]
                lows = [float(b["low"]) for b in window]
                high = max(highs)
                low = min(lows)
                entry = float(trade.entry_price or 0.0)
                peak_price = float(trade.peak_price or 0.0) or (high if side == "long" else low)
                trough_price = float(trade.trough_price or 0.0) or (low if side == "long" else high)
                if side == "long":
                    best_entry = min(lows)
                    best_exit = max(highs)
                    sl = trough_price if 0 < trough_price < entry else low
                    tp = peak_price if peak_price > entry else high
                else:
                    best_entry = max(highs)
                    best_exit = min(lows)
                    sl = peak_price if peak_price > entry else high
                    tp = trough_price if 0 < trough_price < entry else low
                group["best_entry"].append(best_entry)
                group["best_exit"].append(best_exit)
                group["highs"].append(high)
                group["lows"].append(low)
                group["support_levels"].append(low)
                group["resistance_levels"].append(high)
                contract = self._contract(trade.symbol)
                group["stop_usd"].append(abs(entry - sl) * contract)
                group["take_usd"].append(abs(tp - entry) * contract)
            else:
                group["best_entry"].append(float(trade.entry_price or 0.0))
                group["best_exit"].append(float(trade.exit_price or float(trade.entry_price or 0.0)))
                group["highs"].append(float(trade.peak_price or trade.entry_price or 0.0))
                group["lows"].append(float(trade.trough_price or trade.entry_price or 0.0))

            case = None
            if trade.factor_id:
                try:
                    case = self.repository.get_pattern_case_by_factor(trade.factor_id)
                except Exception:
                    case = None
            if case:
                group["support_levels"] = [float(x.get("price")) if isinstance(x, dict) else float(x) for x in (case.support_levels or [])] or group["support_levels"]
                group["resistance_levels"] = [float(x.get("price")) if isinstance(x, dict) else float(x) for x in (case.resistance_levels or [])] or group["resistance_levels"]
                group["fingerprint"] = (case.fingerprint or case.fingerprints or {}) or group["fingerprint"]

        created = 0
        updated = 0
        for key, group in groups.items():
            n = len(group["trades"])
            wins = sum(1 for p in group["pnls"] if p > 0)
            win_rate = round(wins / n * 100.0, 2) if n else 0.0
            sum_pos = sum(p for p in group["pnls"] if p > 0)
            sum_neg = abs(sum(p for p in group["pnls"] if p < 0))
            profit_factor = round(sum_pos / sum_neg, 2) if sum_neg > 0 else 0.0
            stop_usd = _median(group["stop_usd"])
            take_usd = _median(group["take_usd"])
            peaks = [p for p in group["peak_lots"] if p and p > 0]
            partial_tiers: list[dict[str, Any]] = []
            for pct, close_pct in ((40, 30), (60, 30), (80, 20)):
                val = _percentile(peaks, pct)
                if val > 0:
                    partial_tiers.append({"profit_usd_per_lot": round(val, 2), "close_pct": close_pct})
            ladder_tiers: list[dict[str, Any]] = []
            for pct, retrace in ((40, 40), (60, 30), (80, 20), (95, 15)):
                val = _percentile(peaks, pct)
                if val > 0:
                    ladder_tiers.append({"profit_usd_per_lot": round(val, 2), "retrace_pct": retrace})
            entry = _median(group["entry_prices"])
            best_entry = _median(group["best_entry"])
            best_exit = _median(group["best_exit"])
            high = _median(group["highs"])
            low = _median(group["lows"])
            confidence = round(min(0.3 + n * 0.03, 0.95), 2)
            existing = self.repository.get_sltp_case_by_key(key)
            strategy = {
                "stop_method": "learned",
                "take_method": "learned",
                "stop_usd_per_lot": round(stop_usd, 2),
                "take_usd_per_lot": round(take_usd, 2),
                "partial_close_tiers": partial_tiers,
                "ladder_tiers": ladder_tiers,
                "suggested_stop_pct": round(abs(entry - (entry - stop_usd / self._contract(group["symbol"]))) / entry * 100, 4) if entry else 0.0,
                "suggested_take_pct": round(abs((entry + take_usd / self._contract(group["symbol"])) - entry) / entry * 100, 4) if entry else 0.0,
            }
            fields = {
                "strategy_key": key,
                "trade_id": group["trades"][0].id,
                "factor_id": group["factor_id"],
                "symbol": group["symbol"],
                "side": group["side"],
                "entry_price": round(entry, 5),
                "sl_price": round(entry - stop_usd / max(self._contract(group["symbol"]), 1e-9), 5) if group["side"] == "long" else round(entry + stop_usd / max(self._contract(group["symbol"]), 1e-9), 5),
                "tp_price": round(entry + take_usd / max(self._contract(group["symbol"]), 1e-9), 5) if group["side"] == "long" else round(entry - take_usd / max(self._contract(group["symbol"]), 1e-9), 5),
                "stop_usd_per_lot": round(stop_usd, 2),
                "take_usd_per_lot": round(take_usd, 2),
                "best_entry_price": round(best_entry, 5),
                "best_exit_price": round(best_exit, 5),
                "high_price": round(high, 5),
                "low_price": round(low, 5),
                "pattern_fingerprint": group["fingerprint"],
                "features": {
                    "support_levels": group["support_levels"][-5:],
                    "resistance_levels": group["resistance_levels"][-5:],
                    "sample_count": n,
                },
                "partial_close_tiers": partial_tiers,
                "ladder_tiers": ladder_tiers,
                "strategy": strategy,
                "quality": "auto",
                "sample_count": n,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "confidence": confidence,
                "model_version": self.model_version,
                "source": "auto",
                "enabled": True,
            }
            if existing:
                fields.pop("trade_id", None)
                self.repository.update_sltp_case(existing.id, **fields)
                updated += 1
            else:
                record = SltpCaseRecord(id=str(uuid.uuid4()), **fields)
                self.repository.save_sltp_case(record)
                created += 1
        return {"created": created, "updated": updated, "strategies": len(groups)}

    def train(self) -> dict[str, Any]:
        strategies = self.repository.list_sltp_cases(limit=1000)
        version = f"v{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        trained = 0
        for case in strategies:
            confidence = round(min(0.3 + int(case.sample_count or 0) * 0.03, 0.95), 2)
            self.repository.update_sltp_case(
                case.id,
                confidence=confidence,
                model_version=version,
            )
            trained += 1
        self.model_version = version
        self.trained_at = datetime.now(timezone.utc).isoformat()
        return {"trained": trained, "model_version": version, "trained_at": self.trained_at}

    def strategies(self, symbol: str | None = None, side: str | None = None) -> list[dict[str, Any]]:
        rows = self.repository.list_sltp_cases(limit=1000, symbol=symbol, side=side, enabled=True)
        return [r.__dict__ | {"id": r.id} for r in rows]

    def model_status(self) -> dict[str, Any]:
        rows = self.repository.list_sltp_cases(limit=1000)
        confs = [float(r.confidence or 0.0) for r in rows]
        return {
            "model_version": self.model_version,
            "trained_at": self.trained_at,
            "strategy_count": len(rows),
            "avg_confidence": round(sum(confs) / len(confs), 2) if confs else 0.0,
        }

"""因子参数自动优化引擎：多参数组合回测、评分排序、提前停止。"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from backtest_store.engine import Backtester
from market_data.service import MarketDataService
from oems.mt5_gateway import MT5Gateway
from sandbox.runner import execute_factor


@dataclass
class OptimizationConfig:
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    bars: int = 500
    start_time: Any = None
    end_time: Any = None
    initial_equity: float = 10_000.0
    leverage: int = 30
    delay_ms: int = 0
    slippage_points: float = 0.0
    objective: str = "composite"  # win_rate | profit | composite
    target_win_rate_pct: float = 0.0
    target_total_return_pct: float = 0.0
    target_profit_factor: float = 0.0
    target_max_drawdown_pct: float = 100.0
    max_iterations: int = 30
    early_stop_rounds: int = 5
    walk_forward: bool = True
    train_ratio: float = 0.7
    walk_forward_folds: int = 3
    min_trades: int = 0
    complexity_penalty: float = 0.01
    max_deviation_pct: float = 30.0
    fidelity_weight: float = 0.4
    beam_width: int = 10
    search_rounds: int = 5
    deviation_levels: tuple[float, ...] = (0.8, 0.9, 1.0, 1.1, 1.2)
    param_ranges: Optional[dict[str, dict[str, float]]] = None
    seed: int = 202608


class FactorOptimizer:
    """对因子参数组合执行多轮回测，按目标函数返回最优参数。"""

    def __init__(
        self,
        market: MarketDataService,
        gateway: Optional[MT5Gateway] = None,
        progress: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        self.market = market
        self.gateway = gateway
        self.progress = progress


    def optimize(
        self,
        code: str,
        params: dict[str, Any],
        config: OptimizationConfig,
    ) -> dict[str, Any]:
        bars = self._bars(config)
        if not bars:
            return {"ok": False, "message": "无法获取回测数据", "trials": []}
        df = self._dataframe(bars)

        split = int(len(bars) * config.train_ratio) if config.walk_forward and len(bars) >= 50 else len(bars)
        train_bars = bars[:split]
        train_df = df.iloc[:split]
        test_bars = bars[split:]
        test_df = df.iloc[split:]

        bt_params = {
            "initial_equity": config.initial_equity,
            "leverage": config.leverage,
            "execution_delay_bars": self._delay_bars(config),
            "slippage_price": self._slippage_price(config),
        }

        original_execution = execute_factor(code, train_bars, dict(params))
        original_entry = (
            pd.Series(original_execution.entry_values, index=train_df.index) if original_execution.ok else None
        )

        if config.param_ranges:
            candidates = self._sample_candidates(config.param_ranges, config.max_iterations)
            if not candidates:
                candidates = [dict(params)]
            best = None
            satisfied_best = None
            trials = []
            satisfied_count = 0
            no_improve = 0
            stop_reason = "达到最大迭代次数"
            for index, candidate in enumerate(candidates):
                trial = self._evaluate_candidate(
                    code, params, candidate, train_df, train_bars, bt_params, original_entry, config
                )
                if trial is None:
                    continue
                trials.append(trial)
                if best is None or trial["score"] > best["score"]:
                    best = trial
                    no_improve = 0
                else:
                    no_improve += 1
                    if config.early_stop_rounds > 0 and no_improve >= config.early_stop_rounds:
                        stop_reason = f"连续 {config.early_stop_rounds} 轮无改进，提前停止"
                        break
                if trial["satisfies"]:
                    satisfied_count += 1
                    if satisfied_best is None or trial["score"] > satisfied_best["score"]:
                        satisfied_best = trial
                if self.progress:
                    self.progress(index + 1, len(candidates))
        else:
            best, satisfied_best, trials, satisfied_count, stop_reason = self._local_beam_search(
                code, params, train_df, train_bars, bt_params, original_entry, config
            )

        if best is None:
            return {"ok": False, "message": "所有参数组合回测均失败", "trials": trials}
        targets = {
            "win_rate_pct": config.target_win_rate_pct,
            "total_return_pct": config.target_total_return_pct,
            "profit_factor": config.target_profit_factor,
            "max_drawdown_pct": config.target_max_drawdown_pct,
        }
        targets_reached = satisfied_best is not None
        if targets_reached:
            best = satisfied_best
            message = (
                f"优化完成：共尝试 {len(trials)} 组参数，"
                f"{satisfied_count} 组满足全部目标，最优评分 {best['score']}"
            )
        else:
            message = "未找到同时满足全部目标的参数，已返回评分最高的最优参数，请调整目标或扩大参数范围"

        train_metrics = best["metrics"]
        test_metrics = None
        walk_forward_metrics: list[dict[str, Any]] = []
        if config.walk_forward and test_bars:
            execution = execute_factor(code, test_bars, {**params, **best["params"]})
            if execution.ok:
                entry = pd.Series(execution.entry_values, index=test_df.index)
                exit_sig = pd.Series(execution.exit_values, index=test_df.index) if execution.exit_values else None
                test_result = Backtester().run(test_df, entry, exit_sig, bt_params)
                test_metrics = test_result.metrics.model_dump()

            folds = max(int(config.walk_forward_folds), 1)
            segment = len(test_bars) // folds
            if segment >= 20:
                for fold in range(folds):
                    start = fold * segment
                    end = len(test_bars) if fold == folds - 1 else (fold + 1) * segment
                    fold_bars = test_bars[start:end]
                    fold_df = test_df.iloc[start:end]
                    fold_execution = execute_factor(code, fold_bars, {**params, **best["params"]})
                    if fold_execution.ok:
                        entry = pd.Series(fold_execution.entry_values, index=fold_df.index)
                        exit_sig = pd.Series(fold_execution.exit_values, index=fold_df.index) if fold_execution.exit_values else None
                        fold_result = Backtester().run(fold_df, entry, exit_sig, bt_params)
                        walk_forward_metrics.append(fold_result.metrics.model_dump())
            if walk_forward_metrics:
                test_metrics = self._avg_metrics(walk_forward_metrics)

        if config.min_trades > 0 and best["trades"] < config.min_trades:
            targets_reached = False
            message = (
                f"最优参数训练样本交易数 {best['trades']} "
                f"低于最低要求 {config.min_trades}，请调整参数范围或目标"
            )

        trials.sort(key=lambda t: t["score"], reverse=True)
        return {
            "ok": True,
            "message": message,
            "best_params": best["params"],
            "best_metrics": best["metrics"],
            "train_metrics": train_metrics,
            "test_metrics": test_metrics,
            "walk_forward_metrics": walk_forward_metrics,
            "best_trades": best["trades"],
            "score": best["score"],
            "fidelity": round(float(best.get("fidelity", 0.0)), 6),
            "stop_reason": stop_reason,
            "trials": trials[:20],
            "objective": config.objective,
            "targets": targets,
            "targets_reached": targets_reached,
            "satisfied_trials": satisfied_count,
        }

    def _evaluate_candidate(self, code, params, candidate, train_df, train_bars, bt_params, original_entry, config):
        merged = {**params, **candidate}
        execution = execute_factor(code, train_bars, merged)
        if not execution.ok:
            return None
        entry = pd.Series(execution.entry_values, index=train_df.index)
        exit_sig = pd.Series(execution.exit_values, index=train_df.index) if execution.exit_values else None
        result = Backtester().run(train_df, entry, exit_sig, bt_params)
        metrics = result.metrics
        perf = self._score(metrics, config.objective, len(candidate), config.complexity_penalty)
        fidelity = self._fidelity(original_entry, entry)
        score = self._combined_score(perf, fidelity, config.fidelity_weight)
        return {
            "params": candidate,
            "score": round(score, 6),
            "perf_score": round(perf, 6),
            "fidelity": round(fidelity, 6),
            "metrics": metrics.model_dump(),
            "trades": result.metrics.num_trades,
            "satisfies": self._satisfies(metrics, config),
        }

    def _local_beam_search(self, code, params, train_df, train_bars, bt_params, original_entry, config):
        value_lists = self._local_value_lists(params, config)
        if not value_lists:
            first = self._evaluate_candidate(
                code, params, dict(params), train_df, train_bars, bt_params, original_entry, config
            )
            if first is None:
                return None, None, [], 0, "所有参数组合回测均失败"
            return first, first if first["satisfies"] else None, [first], int(first["satisfies"]), "原参数即最优"

        candidates = [dict(params)]
        for key, values in value_lists.items():
            for value in values:
                if value != params[key]:
                    candidates.append({key: value})

        evaluated = {}
        for candidate in candidates:
            trial = self._evaluate_candidate(
                code, params, candidate, train_df, train_bars, bt_params, original_entry, config
            )
            if trial is not None:
                evaluated[self._params_key(candidate)] = trial

        if not evaluated:
            return None, None, [], 0, "所有参数组合回测均失败"

        ranked = sorted(evaluated.values(), key=lambda t: t["score"], reverse=True)
        best = ranked[0]
        beam = [best["params"]]
        max_evals = max(int(config.max_iterations), len(candidates))
        rounds = max(int(config.search_rounds), 0)
        early_stop_rounds = max(int(config.early_stop_rounds), 0)
        no_improve = 0
        best_score = best["score"]
        stop_reason = "达到最大迭代次数"

        for _ in range(rounds):
            if len(evaluated) >= max_evals:
                stop_reason = "达到最大迭代次数"
                break
            neighbors = []
            for member in beam:
                neighbors.extend(self._beam_neighbors(member, value_lists))
            added = 0
            for candidate in neighbors:
                key = self._params_key(candidate)
                if key in evaluated:
                    continue
                if len(evaluated) >= max_evals:
                    break
                trial = self._evaluate_candidate(
                    code, params, candidate, train_df, train_bars, bt_params, original_entry, config
                )
                if trial is not None:
                    evaluated[key] = trial
                    added += 1
            if added == 0:
                stop_reason = "束搜索收敛，参数已稳定"
                break
            ranked = sorted(evaluated.values(), key=lambda t: t["score"], reverse=True)
            new_best_score = ranked[0]["score"]
            if new_best_score <= best_score:
                no_improve += 1
                if early_stop_rounds > 0 and no_improve >= early_stop_rounds:
                    stop_reason = f"连续 {early_stop_rounds} 轮无改进，提前停止"
                    break
            else:
                no_improve = 0
                best_score = new_best_score
            new_beam = [t["params"] for t in ranked[: max(int(config.beam_width), 1)]]
            if new_beam == beam:
                stop_reason = "束搜索收敛，参数已稳定"
                break
            beam = new_beam

        trials = list(evaluated.values())
        ranked = sorted(trials, key=lambda t: t["score"], reverse=True)
        best = ranked[0]
        satisfied = [t for t in ranked if t["satisfies"]]
        satisfied_best = satisfied[0] if satisfied else None
        return best, satisfied_best, trials, len(satisfied), stop_reason

    @staticmethod
    def _params_key(params):
        return tuple(sorted((k, v) for k, v in params.items()))

    @staticmethod
    def _beam_neighbors(params, value_lists, max_pair=40):
        result = []
        for key, values in value_lists.items():
            for value in values:
                if value != params[key]:
                    candidate = dict(params)
                    candidate[key] = value
                    result.append(candidate)
        pairs = []
        keys = list(value_lists)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                for vi in value_lists[keys[i]]:
                    if vi == params[keys[i]]:
                        continue
                    for vj in value_lists[keys[j]]:
                        if vj == params[keys[j]]:
                            continue
                        candidate = dict(params)
                        candidate[keys[i]] = vi
                        candidate[keys[j]] = vj
                        pairs.append(candidate)
        if len(pairs) > max_pair:
            rng = np.random.default_rng(202608)
            indices = rng.choice(len(pairs), size=max_pair, replace=False)
            pairs = [pairs[i] for i in indices]
        result.extend(pairs)
        return result

    @staticmethod
    def _local_value_lists(params, config):
        max_dev = max(float(config.max_deviation_pct), 0.0) / 100.0
        levels = [float(level) for level in config.deviation_levels if abs(float(level) - 1.0) <= max_dev + 1e-9]
        if not levels:
            levels = [1.0]
        value_lists = {}
        for key, value in params.items():
            if key.startswith("_") or isinstance(value, bool) or not isinstance(value, (int, float)) or value == 0:
                continue
            is_int = isinstance(value, int)
            values = []
            for level in levels:
                v = value * level
                if is_int:
                    v = int(round(v))
                else:
                    v = round(v, 6)
                values.append(v)
            values = sorted(set(values))
            if len(values) > 1:
                value_lists[key] = values
        return value_lists

    @staticmethod
    def _fidelity(original, candidate):
        if original is None:
            return 1.0
        a = set(original[original != 0].index.tolist())
        b = set(candidate[candidate != 0].index.tolist())
        if not a:
            return 1.0
        if not b:
            return 0.0
        overlap = len(a & b)
        recall = overlap / len(a)
        precision = overlap / len(b)
        if recall + precision <= 0:
            return 0.0
        return 2.0 * recall * precision / (recall + precision)

    @staticmethod
    def _combined_score(perf_score, fidelity, fidelity_weight):
        weight = min(max(float(fidelity_weight), 0.0), 1.0)
        return perf_score * (1.0 - weight) + fidelity * weight

    def _bars(self, config: OptimizationConfig) -> list[dict[str, Any]]:
        if config.start_time or config.end_time:
            if self.gateway:
                return self.gateway.get_rates_range(config.symbol, config.timeframe, config.start_time, config.end_time)
            return []
        return self.market.get_bars(config.symbol, config.timeframe, limit=config.bars)

    @staticmethod
    def _dataframe(bars: list[dict[str, Any]]) -> pd.DataFrame:
        df = pd.DataFrame(bars)
        df["time"] = pd.to_datetime(df["time"], utc=True)
        return df.set_index("time")

    @staticmethod
    def _auto_ranges(params: dict[str, Any]) -> dict[str, dict[str, float]]:
        ranges: dict[str, dict[str, float]] = {}
        for key, value in params.items():
            if key.startswith("_") or not isinstance(value, (int, float)) or value == 0:
                continue
            is_int = isinstance(value, int) and not isinstance(value, bool)
            step = max(abs(value) * 0.1, 1 if is_int else 0.001)
            ranges[key] = {
                "min": value * 0.5,
                "max": value * 1.5,
                "step": step,
            }
        return ranges

    @staticmethod
    def _sample_candidates(
        ranges: dict[str, dict[str, float]],
        max_iterations: int,
        seed: int = 202608,
    ) -> list[dict[str, Any]]:
        if not ranges:
            return [{}]
        value_lists: dict[str, list[Any]] = {}
        for key, spec in ranges.items():
            low = float(spec.get("min", 0))
            high = float(spec.get("max", 1))
            step = float(spec.get("step", 1))
            if step <= 0 or high < low:
                continue
            count = int((high - low) / step) + 1
            values = [low + step * i for i in range(count)]
            values = [v for v in values if v <= high + 1e-9]
            is_int = low == int(low) and step == int(step)
            value_lists[key] = [int(v) if is_int else round(v, 6) for v in values]
        if not value_lists:
            return [{}]

        keys = list(value_lists)
        combos = list(itertools.product(*(value_lists[k] for k in keys)))
        if len(combos) <= max_iterations:
            sampled = combos
        else:
            rng = np.random.default_rng(seed)
            indices = rng.choice(len(combos), size=max_iterations, replace=False)
            sampled = [combos[i] for i in indices]
        return [dict(zip(keys, combo)) for combo in sampled]

    @staticmethod
    def _score(metrics: Any, objective: str, param_count: int = 0, complexity_penalty: float = 0.0) -> float:
        win_rate = metrics.win_rate_pct
        profit_factor = metrics.profit_factor
        total_return = metrics.total_return_pct
        sharpe = metrics.sharpe
        max_dd = metrics.max_drawdown_pct
        if objective == "win_rate":
            score = win_rate + min(profit_factor, 5) * 0.2
        elif objective == "profit":
            score = total_return * 0.7 + min(profit_factor, 5) * 3 + max(sharpe, 0) * 0.3 - max_dd * 0.2
        else:
            score = (
            (win_rate / 100.0) * 0.4
            + min(profit_factor, 5) / 5.0 * 0.3
            + max(sharpe, 0) / 3.0 * 0.2
            + max(0.0, 1.0 - max_dd / 100.0) * 0.1
        )
        return score - complexity_penalty * max(param_count, 0)

    @staticmethod
    def _avg_metrics(metrics_list: list[dict[str, Any]]) -> dict[str, Any]:
        keys = [
            "total_return_pct",
            "annual_return_pct",
            "sharpe",
            "max_drawdown_pct",
            "win_rate_pct",
            "profit_factor",
            "avg_trade_pct",
            "max_consecutive_losses",
        ]
        avg: dict[str, Any] = {}
        for key in keys:
            values = [m.get(key, 0.0) for m in metrics_list]
            avg[key] = round(sum(values) / len(values), 4) if values else 0.0
        avg["num_trades"] = int(sum(m.get("num_trades", 0) for m in metrics_list))
        return avg

    @staticmethod
    def _satisfies(metrics: Any, config: OptimizationConfig) -> bool:
        if config.target_win_rate_pct > 0 and metrics.win_rate_pct < config.target_win_rate_pct:
            return False
        if config.target_total_return_pct > 0 and metrics.total_return_pct < config.target_total_return_pct:
            return False
        if config.target_profit_factor > 0 and metrics.profit_factor < config.target_profit_factor:
            return False
        if config.target_max_drawdown_pct < 100 and metrics.max_drawdown_pct > config.target_max_drawdown_pct:
            return False
        return True

    @staticmethod
    def _delay_bars(config: OptimizationConfig) -> int:
        bar_seconds = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}.get(
            config.timeframe.upper(), 900
        )
        return math.ceil(config.delay_ms / 1000 / bar_seconds) if config.delay_ms > 0 else 0

    def _slippage_price(self, config: OptimizationConfig) -> float:
        point = 0.00001
        if self.gateway:
            info = self.gateway.get_symbol_info(config.symbol)
            if info:
                point = float(info["point"])
        return config.slippage_points * point

"""板块四：统一 ML 模型池与训练/预测服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from indicators.technical import atr_series, ema, rsi


class TradingModel:
    name = "base"
    description = "基础交易模型"

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        raise NotImplementedError

    def predict(self, df: pd.DataFrame) -> dict[str, Any]:
        raise NotImplementedError


class LogisticMomentumModel(TradingModel):
    """动量 + 波动特征的逻辑回归模型（纯 numpy 实现，不依赖 sklearn）。"""

    name = "logistic_momentum"
    description = "基于动量、RSI、ATR 与均线差的逻辑回归信号模型"

    def __init__(self) -> None:
        self.weights: np.ndarray | None = None
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None
        self.metrics: dict[str, Any] = {}

    def _features(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        return pd.DataFrame(
            {
                "ret5": close.pct_change(5),
                "ret10": close.pct_change(10),
                "rsi": rsi(close, 14) / 100.0 - 0.5,
                "atr_ratio": atr_series(df, 14) / close,
                "ema_spread": (ema(close, 12) - ema(close, 26)) / close,
            }
        )

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        features = self._features(df).dropna()
        if len(features) < 30:
            raise ValueError("样本不足，无法训练逻辑回归模型（至少需要 30 根有效 K 线）")
        target = (df["close"].shift(-1) > df["close"]).astype(float).reindex(features.index).fillna(0.0)
        X = features.to_numpy(dtype=float)
        y = target.to_numpy(dtype=float)

        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0) + 1e-9
        Xs = (X - self.mean) / self.std
        Xs = np.column_stack([np.ones(len(Xs)), Xs])

        weights = np.zeros(Xs.shape[1])
        learning_rate = 0.1
        for _ in range(300):
            prob = 1.0 / (1.0 + np.exp(-Xs @ weights))
            gradient = Xs.T @ (prob - y) / len(y) + 0.01 * weights
            weights -= learning_rate * gradient
        self.weights = weights

        prob = 1.0 / (1.0 + np.exp(-Xs @ weights))
        accuracy = float(((prob > 0.5).astype(float) == y).mean())
        self.metrics = {"accuracy": round(accuracy, 4), "samples": int(len(y))}
        return dict(self.metrics)

    def predict(self, df: pd.DataFrame) -> dict[str, Any]:
        features = self._features(df).dropna()
        if self.weights is None or len(features) == 0:
            return {"signal": "none", "confidence": 0.5, "probability": 0.5}
        row = (features.iloc[-1].to_numpy(dtype=float) - self.mean) / self.std
        prob = float(1.0 / (1.0 + np.exp(-(np.concatenate([[1.0], row]) @ self.weights))))
        signal = "long" if prob >= 0.55 else "short" if prob <= 0.45 else "none"
        return {"signal": signal, "confidence": round(float(max(prob, 1 - prob)), 3), "probability": round(prob, 3)}


class ThresholdMomentumModel(TradingModel):
    """阈值动量模型：用历史收益分布作为训练统计量。"""

    name = "threshold_momentum"
    description = "基于收益 z-score 的阈值动量信号模型"

    def __init__(self) -> None:
        self.mean_ret = 0.0
        self.std_ret = 1.0
        self.metrics: dict[str, Any] = {}

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        rets = df["close"].pct_change().dropna()
        self.mean_ret = float(rets.mean())
        self.std_ret = float(rets.std()) + 1e-9
        self.metrics = {"samples": int(len(rets)), "std_ret": round(self.std_ret, 6)}
        return dict(self.metrics)

    def predict(self, df: pd.DataFrame) -> dict[str, Any]:
        rets = df["close"].pct_change(5).dropna()
        if len(rets) == 0:
            return {"signal": "none", "confidence": 0.5, "z_score": 0.0}
        z = float((rets.iloc[-1] - self.mean_ret * 5) / (self.std_ret * np.sqrt(5)))
        signal = "long" if z >= 0.8 else "short" if z <= -0.8 else "none"
        return {
            "signal": signal,
            "confidence": round(min(0.99, abs(z) / 2.5 + 0.5), 3),
            "z_score": round(z, 3),
        }


class GradientBoostingModel(TradingModel):
    """GBDT 评分因子（sklearn GradientBoostingClassifier）：动量+波动特征，未来 3 根收益方向二分类。

    概率作为评分；signal 由阈值 0.60/0.40 决定（比 logistic 更严）；有效与否由 D5 A/B 报告裁决。
    """

    name = "gbdt_momentum"
    description = "基于 GBDT 的多特征评分因子（sklearn）"

    def __init__(self) -> None:
        self.model: Any = None
        self.metrics: dict[str, Any] = {}

    def _features(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]
        return pd.DataFrame(
            {
                "ret5": close.pct_change(5),
                "ret10": close.pct_change(10),
                "rsi": rsi(close, 14) / 100.0 - 0.5,
                "atr_ratio": atr_series(df, 14) / close,
                "ema_spread": (ema(close, 12) - ema(close, 26)) / close,
                "vol20": close.pct_change().rolling(20).std(),
            }
        )

    def train(self, df: pd.DataFrame) -> dict[str, Any]:
        features = self._features(df).dropna()
        if len(features) < 60:
            raise ValueError("样本不足，无法训练 GBDT（至少需要 60 根有效 K 线）")
        target = (df["close"].shift(-3) > df["close"]).astype(int).reindex(features.index).fillna(0)
        X = features.to_numpy(dtype=float)
        y = target.to_numpy(dtype=int)

        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.metrics import roc_auc_score

        self.model = GradientBoostingClassifier(
            n_estimators=200, learning_rate=0.08, max_depth=3, subsample=0.9, random_state=42
        )
        self.model.fit(X, y)
        prob = self.model.predict_proba(X)[:, 1]
        self.metrics = {
            "auc": round(float(roc_auc_score(y, prob)), 4),
            "ic": round(float(np.corrcoef(prob, y)[0, 1]), 4),
            "accuracy": round(float(((prob > 0.5).astype(int) == y).mean()), 4),
            "samples": int(len(y)),
        }
        return dict(self.metrics)

    def predict(self, df: pd.DataFrame) -> dict[str, Any]:
        features = self._features(df).dropna()
        if self.model is None or len(features) == 0:
            return {"signal": "none", "confidence": 0.5, "probability": 0.5}
        X = features.iloc[-1].to_numpy(dtype=float).reshape(1, -1)
        prob = float(self.model.predict_proba(X)[:, 1][0])
        signal = "long" if prob >= 0.60 else "short" if prob <= 0.40 else "none"
        return {
            "signal": signal,
            "confidence": round(float(max(prob, 1 - prob)), 3),
            "probability": round(prob, 3),
        }


MODEL_REGISTRY: dict[str, type[TradingModel]] = {
    LogisticMomentumModel.name: LogisticMomentumModel,
    ThresholdMomentumModel.name: ThresholdMomentumModel,
    GradientBoostingModel.name: GradientBoostingModel,
}


class ModelPool:
    """模型注册、训练与预测的统一入口。"""

    def __init__(self) -> None:
        self.instances: dict[str, TradingModel] = {name: cls() for name, cls in MODEL_REGISTRY.items()}
        self.trained: dict[str, dict[str, Any]] = {}
        self.versions: dict[str, int] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": model.description,
                "kind": "ml",
                "trained": name in self.trained,
                "metrics": self.trained.get(name),
                "version": self.versions.get(name, 0),
                "history_count": len(self.history.get(name, [])),
            }
            for name, model in self.instances.items()
        ]

    def train(self, name: str, df: pd.DataFrame) -> dict[str, Any]:
        if name not in self.instances:
            raise ValueError(f"模型不存在：{name}")
        metrics = self.instances[name].train(df)
        self.trained[name] = metrics
        version = self.versions.get(name, 0) + 1
        self.versions[name] = version
        data_range = None
        if len(df) > 1:
            data_range = {
                "start": df.index[0].isoformat() if hasattr(df.index[0], "isoformat") else str(df.index[0]),
                "end": df.index[-1].isoformat() if hasattr(df.index[-1], "isoformat") else str(df.index[-1]),
            }
        record = {
            "version": version,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "bars": int(len(df)),
            "data_range": data_range,
            "metrics": metrics,
        }
        self.history.setdefault(name, []).insert(0, record)
        self.history[name] = self.history[name][:20]
        return {"name": name, "metrics": metrics, "version": version, "history": record}

    def predict(self, name: str, df: pd.DataFrame) -> dict[str, Any]:
        if name not in self.instances:
            raise ValueError(f"模型不存在：{name}")
        return {"model": name, "trained": name in self.trained, **self.instances[name].predict(df)}

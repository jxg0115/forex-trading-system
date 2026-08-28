"""因子挖掘引擎：自动搜索候选因子（入场/出场/结构位），评估后进入候选池，由用户审阅后保存。"""

from factor_mining.generator import generate_candidates, TEMPLATES
from factor_mining.evaluator import evaluate_signal, market_tags

__all__ = ["generate_candidates", "TEMPLATES", "evaluate_signal", "market_tags"]
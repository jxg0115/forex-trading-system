"""信号偷看未来校验：给信号加入一根未来 K 线后，历史信号必须保持不变。"""

from __future__ import annotations

from typing import Any

from sandbox.runner import execute_factor


def validate_no_lookahead(
    code: str,
    bars: list[dict[str, Any]],
    params: dict[str, Any] | None = None,
    sample_count: int = 4,
) -> dict[str, Any]:
    """抽样检查信号稳定性，返回中文结论。"""

    n = len(bars)
    if n < 30:
        return {"ok": True, "checked": 0, "message": "数据量不足，跳过偷看未来校验"}

    positions = [max(30, int(n * (i + 1) / (sample_count + 1))) for i in range(sample_count)]
    checked = 0
    violations: list[dict[str, Any]] = []
    for pos in positions:
        if pos + 1 >= n:
            continue
        before = execute_factor(code, bars[:pos], params)
        after = execute_factor(code, bars[: pos + 1], params)
        if not before.ok or not after.ok:
            continue
        if not before.entry_values or not after.entry_values:
            continue
        checked += 1
        signal_before = before.entry_values[-1]
        signal_after = after.entry_values[-2]
        same = (signal_before in (None, 0) and signal_after in (None, 0)) or (
            signal_before is not None and signal_after is not None and abs(signal_before - signal_after) < 1e-9
        )
        if not same:
            violations.append({"position": pos, "before": signal_before, "after": signal_after})

    return {
        "ok": not violations,
        "checked": checked,
        "violations": violations,
        "message": "未发现未来数据泄漏" if not violations else f"发现 {len(violations)} 处信号闪烁，请检查 rolling 窗口是否缺少 shift(1)",
    }


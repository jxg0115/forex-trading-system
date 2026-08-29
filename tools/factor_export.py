"""因子数据备份与导出（每次升级前/定期执行）

双轨：
1. DB 完整备份：用 SQLite 在线备份 API 复制 data/factor_store.db 到外部备份目录
   （C:\\Users\\xg\\Documents\\外汇交易系统-backups\\），后端运行中也能安全执行；
2. 结构化导出：factors（正式因子）+ mining_candidates（挖掘候选）全量字段导出为 JSON
   到项目内 exports\\，随 git 提交推送到 GitHub（可读、可追溯、可回滚看因子状态）。

用法：python tools/factor_export.py
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "factor_store.db"
BACKUP_DIR = Path(r"C:\Users\xg\Documents\外汇交易系统-backups")
EXPORT_DIR = ROOT / "exports"

FACTOR_FIELDS = [
    "id", "name", "description", "code", "symbol", "timeframe", "source", "model",
    "status", "version", "params", "tags", "chart_stats", "prompt_snapshot",
    "generated_region", "backtest_stats", "created_at", "updated_at",
    "sl_tp_strategy", "market_adapt",
]
CANDIDATE_FIELDS = [
    "id", "signature", "template", "name", "kind", "variant", "params", "code",
    "symbol", "timeframe", "ic", "icir", "hit_rate", "score", "signal_bars",
    "groups", "backtest", "status", "saved_factor_id", "created_at", "updated_at",
    "eval_bars", "bt_bars", "wf", "sample_note", "ic_tstat", "n_pairs",
    "gate", "gate_reason", "score_ic", "dsr", "t_adj", "t_required",
]


def stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def rows_as_dicts(con: sqlite3.Connection, table: str, fields: list[str]) -> list[dict]:
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    sel = [c for c in fields if c in cols]
    out = []
    for row in con.execute(f"SELECT {', '.join(sel)} FROM {table} ORDER BY created_at"):
        out.append({k: v for k, v in zip(sel, row)})
    return out


def backup_db(keep: int = 14) -> Path:
    """在线备份 DB，并保留最近 keep 份（旧备份自动清理）。启动自动备份也走这里。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(DB)
    dst_path = BACKUP_DIR / f"factor_store_{stamp()}.db"
    try:
        dst = sqlite3.connect(dst_path)
        try:
            src.backup(dst)  # 在线备份：后端写入中也能得到一致快照
            print(f"DB 备份完成 -> {dst_path}")
        finally:
            dst.close()
    finally:
        src.close()
    # 保留策略：按日去重——同日只留最新一份，跨日保留最近 keep 个不同日期（真窗口）
    removed = clean_old_backups(keep)
    if removed:
        print(f"清理重复/超窗备份 {removed} 份（按日去重保留最近 {keep} 个日期）")
    return dst_path


def clean_old_backups(keep: int = 14) -> int:
    """按日去重清理：同日只留最新，跨日保留最近 keep 个不同日期。返回删除数。"""
    olds = sorted(BACKUP_DIR.glob("factor_store_*.db"))
    latest_by_day: dict[str, Path] = {}
    for f in olds:
        parts = f.name.split("_")
        if len(parts) >= 4:
            latest_by_day[parts[2]] = f  # YYYYMMDD -> 同日最新（sorted 升序，后写覆盖）
    keep_paths = {latest_by_day[d] for d in sorted(latest_by_day)[-keep:]}
    removed = 0
    for f in olds:
        if f not in keep_paths:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def export_json() -> Path:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    try:
        payload = {
            "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source_db": str(DB),
            "factors": rows_as_dicts(con, "factors", FACTOR_FIELDS),
            "mining_candidates": rows_as_dicts(con, "mining_candidates", CANDIDATE_FIELDS),
        }
    finally:
        con.close()
    out = EXPORT_DIR / f"factors_{stamp()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"清单导出完成 -> {out}（factors={len(payload['factors'])}，candidates={len(payload['mining_candidates'])}）")
    return out


if __name__ == "__main__":
    backup_db()
    export_json()
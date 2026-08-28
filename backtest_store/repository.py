"""因子特征库：SQLAlchemy 模型与仓储操作，默认 SQLite，可切换 PostgreSQL。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, create_engine, or_, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import settings
from models.factor import FactorCreate, FactorOut


class Base(DeclarativeBase):
    pass


class FactorRecord(Base):
    __tablename__ = "factors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(2000), default="")
    code: Mapped[str] = mapped_column(String(20000))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    source: Mapped[str] = mapped_column(String(32), default="ai")
    model: Mapped[str] = mapped_column(String(64), default="simulated")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    chart_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    prompt_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_region: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    backtest_stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    sl_tp_strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    market_adapt: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    def to_out(self) -> FactorOut:
        return FactorOut(
            id=self.id,
            name=self.name,
            description=self.description,
            code=self.code,
            symbol=self.symbol,
            timeframe=self.timeframe,
            source=self.source,
            model=self.model,
            status=self.status,  # type: ignore[arg-type]
            version=self.version,
            params=self.params or {},
            tags=self.tags or [],
            chart_stats=self.chart_stats or {},
            prompt_snapshot=self.prompt_snapshot or {},
            generated_region=self.generated_region or {},
            backtest_stats=self.backtest_stats,
            sl_tp_strategy=self.sl_tp_strategy or {},
            market_adapt=self.market_adapt or {},
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mt5_ticket: Mapped[str] = mapped_column(String(32), default="", index=True)
    account_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    factor_id: Mapped[str] = mapped_column(String(36), index=True)
    factor_name: Mapped[str] = mapped_column(String(120), default="")
    symbol: Mapped[str] = mapped_column(String(32), default="")
    side: Mapped[str] = mapped_column(String(8), default="")
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(default=0.0)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(nullable=True)
    lots: Mapped[float] = mapped_column(default=0.0)
    pnl: Mapped[float | None] = mapped_column(nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32), default="")
    report_markdown: Mapped[str] = mapped_column(String(20000), default="")
    peak_pnl: Mapped[float | None] = mapped_column(nullable=True)
    trough_pnl: Mapped[float | None] = mapped_column(nullable=True)
    peak_price: Mapped[float | None] = mapped_column(nullable=True)
    trough_price: Mapped[float | None] = mapped_column(nullable=True)
    peak_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trough_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quality: Mapped[str] = mapped_column(String(16), default="")
    optimization: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AlertRecord(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    title: Mapped[str] = mapped_column(String(120), default="")
    message: Mapped[str] = mapped_column(String(4000), default="")
    delivered_to: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class OrderLogRecord(Base):
    __tablename__ = "order_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mt5_ticket: Mapped[str] = mapped_column(String(32), default="", index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8), default="")
    order_type: Mapped[str] = mapped_column(String(24), default="market")
    volume: Mapped[float] = mapped_column(default=0.0)
    price: Mapped[float | None] = mapped_column(nullable=True)
    stoplimit_price: Mapped[float | None] = mapped_column(nullable=True)
    sl: Mapped[float | None] = mapped_column(nullable=True)
    tp: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="submitted", index=True)
    action: Mapped[str] = mapped_column(String(16), default="open")
    factor_id: Mapped[str] = mapped_column(String(36), default="")
    factor_name: Mapped[str] = mapped_column(String(120), default="")
    reason: Mapped[str] = mapped_column(String(500), default="")
    message: Mapped[str] = mapped_column(String(1000), default="")
    idempotency_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PatternCaseRecord(Base):
    __tablename__ = "pattern_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    factor_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    time_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price_top: Mapped[float] = mapped_column(default=0.0)
    price_bottom: Mapped[float] = mapped_column(default=0.0)
    bar_count: Mapped[int] = mapped_column(Integer, default=0)
    fingerprint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fingerprints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ohlcv: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    region_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    entry_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    exit_points: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    support_levels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    resistance_levels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    learned: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pattern_type: Mapped[str] = mapped_column(String(32), default="通用形态")
    pattern_subtype: Mapped[str] = mapped_column(String(32), default="自定义")
    recognition_confidence: Mapped[float] = mapped_column(default=0.0)
    human_confirmed: Mapped[bool] = mapped_column(default=False)
    labels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SltpCaseRecord(Base):
    __tablename__ = "sltp_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(36), index=True)
    factor_id: Mapped[str] = mapped_column(String(36), default="")
    strategy_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))
    entry_price: Mapped[float] = mapped_column(default=0.0)
    sl_price: Mapped[float] = mapped_column(default=0.0)
    tp_price: Mapped[float] = mapped_column(default=0.0)
    stop_usd_per_lot: Mapped[float] = mapped_column(default=0.0)
    take_usd_per_lot: Mapped[float] = mapped_column(default=0.0)
    best_entry_price: Mapped[float | None] = mapped_column(nullable=True)
    best_exit_price: Mapped[float | None] = mapped_column(nullable=True)
    high_price: Mapped[float | None] = mapped_column(nullable=True)
    low_price: Mapped[float | None] = mapped_column(nullable=True)
    pattern_fingerprint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    partial_close_tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    ladder_tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    strategy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality: Mapped[str] = mapped_column(String(16), default="")
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    win_rate: Mapped[float] = mapped_column(default=0.0)
    profit_factor: Mapped[float] = mapped_column(default=0.0)
    confidence: Mapped[float] = mapped_column(default=0.0)
    model_version: Mapped[str] = mapped_column(String(32), default="v0")
    source: Mapped[str] = mapped_column(String(32), default="auto")
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class SystemStateRecord(Base):
    """系统运行状态单例记录：持久化实盘匹配开关与完整执行参数。"""

    __tablename__ = "system_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    matcher_running: Mapped[bool] = mapped_column(default=False)
    symbol: Mapped[str] = mapped_column(String(32), default="")
    timeframe: Mapped[str] = mapped_column(String(8), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sltp_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MiningCandidateRecord(Base):
    """因子挖掘候选：挖掘引擎产出的候选因子（默认 pending，审阅后保存为正式因子）。"""

    __tablename__ = "mining_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    signature: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    template: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(8))  # entry | exit
    variant: Mapped[str] = mapped_column(String(16))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    code: Mapped[str] = mapped_column(String(20000))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    ic: Mapped[float] = mapped_column(default=0.0)
    icir: Mapped[float] = mapped_column(default=0.0)
    hit_rate: Mapped[float] = mapped_column(default=0.0)
    score: Mapped[float] = mapped_column(default=0.0)
    signal_bars: Mapped[int] = mapped_column(default=0)
    eval_bars: Mapped[int] = mapped_column(default=0)
    bt_bars: Mapped[int] = mapped_column(default=0)
    ic_tstat: Mapped[float] = mapped_column(default=0.0)
    n_pairs: Mapped[int] = mapped_column(default=0)
    gate: Mapped[str] = mapped_column(String(16), default="")   # passed=达门槛 / ungated=未达（keep_ungated 时入库）
    gate_reason: Mapped[str] = mapped_column(String(200), default="")
    score_ic: Mapped[float] = mapped_column(default=0.0)        # 原 IC 分（参考）
    dsr: Mapped[float] = mapped_column(default=0.5)             # Deflated Sharpe Ratio（过拟合概率，越高越好）
    t_adj: Mapped[float] = mapped_column(default=0.0)           # 候选 t / Harvey 修正门槛 比值（>1 通过多重检验）
    t_required: Mapped[float] = mapped_column(default=2.0)      # Harvey 修正后要求的 |t| 门槛
    wf: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sample_note: Mapped[str] = mapped_column(String(200), default="")
    groups: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    backtest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    saved_factor_id: Mapped[str] = mapped_column(String(36), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AiConfigRecord(Base):
    """AI 管理配置：名称备注、模型、API Key、负责板块与启用状态。"""

    __tablename__ = "ai_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(16), default="openai")
    model: Mapped[str] = mapped_column(String(64), default="qwen3.8-max")
    api_key: Mapped[str] = mapped_column(String(256), default="")
    base_url: Mapped[str] = mapped_column(String(256), default="")
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


_engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {})
_SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def _ensure_schema() -> None:
    """兼容旧库：为 order_logs 补充新增列。"""

    Base.metadata.create_all(_engine)
    if not settings.database_url.startswith("sqlite"):
        return
    with _engine.connect() as conn:
        order_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(order_logs)"))}
        for column, ddl in (
            ("request_payload", "JSON"),
            ("response_data", "JSON"),
            ("idempotency_key", "VARCHAR(64)"),
        ):
            if column not in order_columns:
                conn.execute(text(f"ALTER TABLE order_logs ADD COLUMN {column} {ddl}"))
        pattern_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(pattern_cases)"))}
        if "statistics" not in pattern_columns:
            conn.execute(text("ALTER TABLE pattern_cases ADD COLUMN statistics JSON"))
        for column in ("support_levels", "resistance_levels", "fingerprints", "features", "ohlcv", "pattern_type", "pattern_subtype", "recognition_confidence", "human_confirmed", "labels"):
            if column not in pattern_columns:
                ddl = "JSON" if column not in ("pattern_type", "pattern_subtype") else "VARCHAR(32)"
                if column == "recognition_confidence":
                    ddl = "FLOAT"
                if column == "human_confirmed":
                    ddl = "BOOLEAN"
                conn.execute(text(f"ALTER TABLE pattern_cases ADD COLUMN {column} {ddl}"))
        trade_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(trades)"))}
        if "mt5_ticket" not in trade_columns:
            conn.execute(text("ALTER TABLE trades ADD COLUMN mt5_ticket VARCHAR(32)"))
        if "account_id" not in trade_columns:
            conn.execute(text("ALTER TABLE trades ADD COLUMN account_id VARCHAR(32)"))
        for column in ("peak_pnl", "trough_pnl", "peak_price", "trough_price"):
            if column not in trade_columns:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {column} FLOAT"))
        for column in ("peak_time", "trough_time"):
            if column not in trade_columns:
                conn.execute(text(f"ALTER TABLE trades ADD COLUMN {column} DATETIME"))
        if "quality" not in trade_columns:
            conn.execute(text("ALTER TABLE trades ADD COLUMN quality VARCHAR(16)"))
        if "optimization" not in trade_columns:
            conn.execute(text("ALTER TABLE trades ADD COLUMN optimization JSON"))
        factor_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(factors)"))}
        if "sl_tp_strategy" not in factor_columns:
            conn.execute(text("ALTER TABLE factors ADD COLUMN sl_tp_strategy JSON"))
        if "market_adapt" not in factor_columns:
            conn.execute(text("ALTER TABLE factors ADD COLUMN market_adapt JSON"))
        state_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(system_states)"))}
        if "sltp_policy" not in state_columns:
            conn.execute(text("ALTER TABLE system_states ADD COLUMN sltp_policy JSON"))
        mining_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(mining_candidates)"))}
        if mining_columns:
            for column, ddl in (
                ("eval_bars", "INTEGER"),
                ("bt_bars", "INTEGER"),
                ("ic_tstat", "FLOAT"),
                ("n_pairs", "INTEGER"),
                ("gate", "VARCHAR(16)"),
                ("gate_reason", "VARCHAR(200)"),
                ("score_ic", "FLOAT"),
                ("dsr", "FLOAT"),
                ("t_adj", "FLOAT"),
                ("t_required", "FLOAT"),
                ("wf", "JSON"),
                ("sample_note", "VARCHAR(200)"),
            ):
                if column not in mining_columns:
                    conn.execute(text(f"ALTER TABLE mining_candidates ADD COLUMN {column} {ddl}"))
        sltp_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(sltp_cases)"))}
        for column, ddl in (
            ("strategy_key", "VARCHAR(64)"),
            ("best_entry_price", "FLOAT"),
            ("best_exit_price", "FLOAT"),
            ("high_price", "FLOAT"),
            ("low_price", "FLOAT"),
            ("pattern_fingerprint", "JSON"),
            ("features", "JSON"),
            ("partial_close_tiers", "JSON"),
            ("ladder_tiers", "JSON"),
            ("sample_count", "INTEGER"),
            ("win_rate", "FLOAT"),
            ("profit_factor", "FLOAT"),
            ("confidence", "FLOAT"),
            ("model_version", "VARCHAR(32)"),
            ("source", "VARCHAR(32)"),
            ("enabled", "BOOLEAN"),
        ):
            if column not in sltp_columns:
                conn.execute(text(f"ALTER TABLE sltp_cases ADD COLUMN {column} {ddl}"))
        conn.commit()


def get_repository() -> "FactorRepository":
    _ensure_schema()
    return FactorRepository(_SessionLocal())


class FactorRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def close(self) -> None:
        self.session.close()

    def save_system_state(self, matcher_running: bool, symbol: str, timeframe: str, config: dict[str, Any]) -> None:
        """保存实盘匹配开关与完整配置，供后端重启后自动恢复。"""

        record = self.session.get(SystemStateRecord, 1)
        if record is None:
            record = SystemStateRecord(
                id=1,
                matcher_running=matcher_running,
                symbol=symbol,
                timeframe=timeframe,
                config=config,
            )
            self.session.add(record)
        else:
            record.matcher_running = matcher_running
            record.symbol = symbol
            record.timeframe = timeframe
            record.config = config
            record.updated_at = datetime.now(timezone.utc)
        self.session.commit()

    def get_system_state(self) -> dict[str, Any] | None:
        record = self.session.get(SystemStateRecord, 1)
        if not record:
            return None
        return {
            "matcher_running": bool(record.matcher_running),
            "symbol": record.symbol or "",
            "timeframe": record.timeframe or "",
            "config": record.config or {},
            "updated_at": record.updated_at,
        }

    def save_sltp_policy(self, policy: dict[str, Any]) -> None:
        """持久化统一止损止盈政策（SystemStateRecord.sltp_policy），后端重启后自动恢复。"""

        record = self.session.get(SystemStateRecord, 1)
        if record is None:
            record = SystemStateRecord(id=1, sltp_policy=policy)
            self.session.add(record)
        else:
            record.sltp_policy = policy
            record.updated_at = datetime.now(timezone.utc)
        self.session.commit()

    def get_sltp_policy(self) -> dict[str, Any] | None:
        record = self.session.get(SystemStateRecord, 1)
        if not record:
            return None
        return record.sltp_policy or None

    # ---------- 因子挖掘候选池 ----------

    def save_mining_records(self, records: list[dict[str, Any]]) -> int:
        """批量入库挖掘候选：按 signature 唯一，已存在则更新评估结果，否则新增。

        整批提交失败（如单条数据异常）时回滚后逐条提交，跳过异常记录，
        避免本会话卡在 PendingRollbackState 影响后续查询。
        """
        saved = 0
        pending: list[MiningCandidateRecord] = []
        for data in records:
            record = self.session.query(MiningCandidateRecord).filter_by(signature=data.get("signature")).first()
            if record is None:
                record = MiningCandidateRecord(id=str(uuid.uuid4()), **{k: v for k, v in data.items() if k in MiningCandidateRecord.__table__.columns.keys()})
                self.session.add(record)
            else:
                record.template = data.get("template") or record.template
                record.name = data.get("name") or record.name
                record.kind = data.get("kind") or record.kind
                record.variant = data.get("variant") or record.variant
                record.params = data.get("params") or record.params
                record.code = data.get("code") or record.code
                record.symbol = data.get("symbol") or record.symbol
                record.timeframe = data.get("timeframe") or record.timeframe
                record.ic = float(data.get("ic") or 0.0)
                record.icir = float(data.get("icir") or 0.0)
                record.hit_rate = float(data.get("hit_rate") or 0.0)
                record.score = float(data.get("score") or 0.0)
                record.signal_bars = int(data.get("signal_bars") or 0)
                record.eval_bars = int(data.get("eval_bars") or 0)
                record.bt_bars = int(data.get("bt_bars") or 0)
                record.ic_tstat = float(data.get("ic_tstat") or 0.0)
                record.n_pairs = int(data.get("n_pairs") or 0)
                record.gate = data.get("gate") or ""
                record.gate_reason = data.get("gate_reason") or ""
                record.score_ic = float(data.get("score_ic") or 0.0)
                record.dsr = float(data.get("dsr") or 0.5) if data.get("dsr") is not None else 0.5
                record.t_adj = float(data.get("t_adj") or 0.0)
                record.t_required = float(data.get("t_required") or 2.0) if data.get("t_required") is not None else 2.0
                record.wf = data.get("wf") or {}
                record.sample_note = data.get("sample_note") or ""
                record.groups = data.get("groups") or {}
                record.backtest = data.get("backtest") or {}
            saved += 1
            pending.append(record)
        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            saved = 0
            for record in pending:
                try:
                    self.session.add(record)
                    self.session.commit()
                    saved += 1
                except Exception:
                    self.session.rollback()
        return saved

    def list_mining_candidates(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 100,
        kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query = self.session.query(MiningCandidateRecord).order_by(MiningCandidateRecord.score.desc(), MiningCandidateRecord.created_at.desc())
        if status:
            query = query.filter(MiningCandidateRecord.status == status)
        if symbol:
            query = query.filter(MiningCandidateRecord.symbol == symbol)
        if kind:
            query = query.filter(MiningCandidateRecord.kind == kind)
        out = []
        for r in query.limit(max(limit, 1)).all():
            out.append(self._mining_out(r))
        return out

    def get_mining_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        record = self.session.get(MiningCandidateRecord, candidate_id)
        return self._mining_out(record) if record else None

    def count_mining_candidates(self, status: str | None = None) -> int:
        query = self.session.query(MiningCandidateRecord)
        if status:
            query = query.filter(MiningCandidateRecord.status == status)
        return int(query.count())

    def update_mining_status(self, candidate_id: str, status: str, saved_factor_id: str = "") -> dict[str, Any] | None:
        record = self.session.get(MiningCandidateRecord, candidate_id)
        if not record:
            return None
        record.status = status
        record.saved_factor_id = saved_factor_id
        record.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return self._mining_out(record)

    @staticmethod
    def _mining_out(r: MiningCandidateRecord) -> dict[str, Any]:
        return {
            "id": r.id,
            "signature": r.signature,
            "template": r.template,
            "name": r.name,
            "kind": r.kind,
            "variant": r.variant,
            "params": r.params or {},
            "code": r.code or "",
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "ic": r.ic,
            "icir": r.icir,
            "hit_rate": r.hit_rate,
            "score": r.score,
            "ic_tstat": r.ic_tstat,
            "n_pairs": r.n_pairs,
            "gate": r.gate or "",
            "gate_reason": r.gate_reason or "",
            "score_ic": r.score_ic,
            "dsr": r.dsr,
            "t_adj": r.t_adj,
            "t_required": r.t_required,
            "signal_bars": r.signal_bars,
            "eval_bars": r.eval_bars,
            "bt_bars": r.bt_bars,
            "wf": r.wf or {},
            "sample_note": r.sample_note or "",
            "groups": r.groups or {},
            "backtest": r.backtest or {},
            "status": r.status,
            "saved_factor_id": r.saved_factor_id or "",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    def list_ai_configs(self) -> list[dict[str, Any]]:
        records = self.session.query(AiConfigRecord).order_by(AiConfigRecord.created_at.asc()).all()
        return [self._ai_config_out(r) for r in records]

    def get_ai_config(self, config_id: str) -> dict[str, Any] | None:
        record = self.session.get(AiConfigRecord, config_id)
        return self._ai_config_out(record) if record else None

    def get_ai_config_secret(self, config_id: str) -> dict[str, Any] | None:
        """返回含完整 API Key 的配置，仅供后端 AI 服务内部使用。"""

        record = self.session.get(AiConfigRecord, config_id)
        if not record:
            return None
        return {
            "id": record.id,
            "name": record.name,
            "provider": record.provider,
            "model": record.model,
            "api_key": record.api_key or "",
            "base_url": record.base_url or "",
            "roles": record.roles or [],
            "enabled": bool(record.enabled),
        }

    def get_enabled_ai_configs(self) -> list[dict[str, Any]]:
        records = (
            self.session.query(AiConfigRecord)
            .filter(AiConfigRecord.enabled.is_(True))
            .order_by(AiConfigRecord.created_at.asc())
            .all()
        )
        return [self._ai_config_out(r) for r in records]

    def create_ai_config(
        self,
        name: str,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        roles: list[str],
        enabled: bool = True,
    ) -> dict[str, Any]:
        record = AiConfigRecord(
            id=str(uuid.uuid4()),
            name=name,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            roles=roles,
            enabled=enabled,
        )
        self.session.add(record)
        self.session.commit()
        return self._ai_config_out(record)

    def update_ai_config(
        self,
        config_id: str,
        name: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        roles: list[str] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        record = self.session.get(AiConfigRecord, config_id)
        if not record:
            return None
        if name is not None:
            record.name = name
        if provider is not None:
            record.provider = provider
        if model is not None:
            record.model = model
        if api_key is not None and api_key.strip():
            record.api_key = api_key.strip()
        if base_url is not None:
            record.base_url = base_url.strip()
        if roles is not None:
            record.roles = roles
        if enabled is not None:
            record.enabled = enabled
        record.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return self._ai_config_out(record)

    def delete_ai_config(self, config_id: str) -> bool:
        record = self.session.get(AiConfigRecord, config_id)
        if not record:
            return False
        self.session.delete(record)
        self.session.commit()
        return True

    @staticmethod
    def _ai_config_out(record: AiConfigRecord) -> dict[str, Any]:
        key = record.api_key or ""
        masked = f"{key[:4]}****{key[-4:]}" if len(key) > 8 else ("已设置" if key else "")
        return {
            "id": record.id,
            "name": record.name,
            "provider": record.provider,
            "model": record.model,
            "api_key_masked": masked,
            "base_url": record.base_url,
            "roles": record.roles or [],
            "enabled": bool(record.enabled),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }

    def create_factor(self, data: FactorCreate) -> FactorOut:
        record = FactorRecord(
            id=str(uuid.uuid4()),
            name=data.name,
            description=data.description,
            code=data.code,
            symbol=data.symbol,
            timeframe=data.timeframe,
            source=data.source,
            model=data.model,
            params=data.params,
            tags=data.tags,
            chart_stats=data.chart_stats,
            prompt_snapshot=data.prompt_snapshot,
            generated_region=data.generated_region,
            backtest_stats=data.backtest_stats,
            sl_tp_strategy=data.sl_tp_strategy or {},
            market_adapt=data.market_adapt or {},
        )
        self.session.add(record)
        self.session.commit()
        return record.to_out()

    def list_factors(self, status: str | None = None, symbol: str | None = None) -> list[FactorOut]:
        query = self.session.query(FactorRecord).order_by(FactorRecord.created_at.desc())
        if status:
            query = query.filter(FactorRecord.status == status)
        if symbol:
            query = query.filter(FactorRecord.symbol == symbol)
        return [r.to_out() for r in query.all()]

    def get_factor(self, factor_id: str) -> FactorOut | None:
        record = self.session.get(FactorRecord, factor_id)
        return record.to_out() if record else None

    def update_status(self, factor_id: str, status: str) -> FactorOut | None:
        record = self.session.get(FactorRecord, factor_id)
        if not record:
            return None
        record.status = status
        record.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return record.to_out()

    def update_backtest(self, factor_id: str, stats: dict[str, Any]) -> FactorOut | None:
        record = self.session.get(FactorRecord, factor_id)
        if not record:
            return None
        record.backtest_stats = stats
        record.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return record.to_out()

    def update_factor(self, factor_id: str, data: FactorCreate) -> FactorOut | None:
        record = self.session.get(FactorRecord, factor_id)
        if not record:
            return None
        for field in (
            "name",
            "description",
            "code",
            "symbol",
            "timeframe",
            "source",
            "model",
            "params",
            "tags",
            "chart_stats",
            "prompt_snapshot",
            "generated_region",
            "backtest_stats",
            "sl_tp_strategy",
            "market_adapt",
        ):
            setattr(record, field, getattr(data, field))
        record.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return record.to_out()

    def delete_factor(self, factor_id: str) -> bool:
        record = self.session.get(FactorRecord, factor_id)
        if not record:
            return False
        self.session.delete(record)
        self.session.commit()
        return True

    def clear_factors(self) -> int:
        count = self.session.query(FactorRecord).delete()
        self.session.commit()
        return count

    def save_trade(self, trade: TradeRecord) -> TradeRecord:
        self.session.add(trade)
        self.session.commit()
        return trade

    def update_trade(self, trade_id: str, **fields: Any) -> bool:
        record = self.session.get(TradeRecord, trade_id)
        if not record:
            return False
        for key, value in fields.items():
            if hasattr(record, key):
                setattr(record, key, value)
        self.session.commit()
        return True

    def save_alert(self, alert: AlertRecord) -> AlertRecord:
        self.session.add(alert)
        self.session.commit()
        return alert

    def list_trades(self, limit: int = 50) -> list[TradeRecord]:
        return self.session.query(TradeRecord).order_by(TradeRecord.entry_time.desc()).limit(limit).all()

    def list_trades_filtered(
        self,
        symbol: str | None = None,
        account_id: str | None = None,
        side: str | None = None,
        quality: str | None = None,
        min_lots: float | None = None,
        max_lots: float | None = None,
        fixed_lots: float | None = None,
        start_time=None,
        end_time=None,
        limit: int = 10000,
    ) -> list[TradeRecord]:
        query = self.session.query(TradeRecord)
        if symbol:
            query = query.filter(TradeRecord.symbol == symbol)
        if account_id:
            query = query.filter(TradeRecord.account_id == account_id)
        if side:
            query = query.filter(TradeRecord.side.in_([side, "buy" if side == "long" else "sell"]))
        if quality:
            query = query.filter(TradeRecord.quality == quality)
        if fixed_lots is not None:
            query = query.filter((TradeRecord.lots >= fixed_lots * 0.99) & (TradeRecord.lots <= fixed_lots * 1.01))
        else:
            if min_lots is not None:
                query = query.filter(TradeRecord.lots >= min_lots)
            if max_lots is not None:
                query = query.filter(TradeRecord.lots <= max_lots)
        if start_time is not None:
            query = query.filter(TradeRecord.entry_time >= start_time)
        if end_time is not None:
            query = query.filter(TradeRecord.entry_time <= end_time)
        return query.order_by(TradeRecord.entry_time.desc()).limit(limit).all()

    def get_trades_by_ids(self, trade_ids: list[str]) -> list[TradeRecord]:
        if not trade_ids:
            return []
        return self.session.query(TradeRecord).filter(TradeRecord.id.in_(trade_ids)).all()

    def get_trade_tickets(self) -> set[str]:
        rows = self.session.query(TradeRecord.mt5_ticket).filter(TradeRecord.mt5_ticket != "").all()
        return {str(r[0]) for r in rows}

    def list_account_ids(self) -> list[str]:
        rows = self.session.query(TradeRecord.account_id).filter(TradeRecord.account_id != "").distinct().all()
        return sorted({str(r[0]) for r in rows})

    def backfill_account_id(self, account_id: str) -> int:
        account_id = str(account_id or "")
        if not account_id:
            return 0
        rows = (
            self.session.query(TradeRecord)
            .filter(or_(TradeRecord.account_id.is_(None), TradeRecord.account_id == ""))
            .update({"account_id": account_id}, synchronize_session=False)
        )
        self.session.commit()
        return int(rows)

    def reassign_account_id(self, account_id: str) -> int:
        account_id = str(account_id or "")
        if not account_id:
            return 0
        rows = self.session.query(TradeRecord).update({"account_id": account_id}, synchronize_session=False)
        self.session.commit()
        return int(rows)

    def save_sltp_case(self, record: SltpCaseRecord) -> SltpCaseRecord:
        if not record.strategy_key:
            record.strategy_key = f"{record.symbol}:{record.side}:{record.factor_id or 'manual'}:{record.id[:8]}"
        self.session.add(record)
        self.session.commit()
        return record

    def get_sltp_case_by_key(self, strategy_key: str) -> SltpCaseRecord | None:
        return self.session.query(SltpCaseRecord).filter(SltpCaseRecord.strategy_key == strategy_key).first()

    def update_sltp_case(self, case_id: str, **fields: Any) -> bool:
        record = self.session.get(SltpCaseRecord, case_id)
        if not record:
            return False
        for key, value in fields.items():
            if hasattr(record, key):
                setattr(record, key, value)
        self.session.commit()
        return True

    def list_sltp_cases(
        self,
        limit: int = 200,
        symbol: str | None = None,
        side: str | None = None,
        enabled: bool | None = None,
    ) -> list[SltpCaseRecord]:
        query = self.session.query(SltpCaseRecord)
        if symbol:
            query = query.filter(SltpCaseRecord.symbol == symbol)
        if side:
            query = query.filter(SltpCaseRecord.side == side)
        if enabled is not None:
            query = query.filter(SltpCaseRecord.enabled.is_(enabled))
        return query.order_by(SltpCaseRecord.created_at.desc()).limit(limit).all()

    def delete_trades_by_ids(self, trade_ids: list[str]) -> int:
        if not trade_ids:
            return 0
        deleted = self.session.query(TradeRecord).filter(TradeRecord.id.in_(trade_ids)).delete(synchronize_session=False)
        self.session.commit()
        return deleted

    def list_alerts(self, limit: int = 50) -> list[AlertRecord]:
        return self.session.query(AlertRecord).order_by(AlertRecord.created_at.desc()).limit(limit).all()

    def save_order_log(self, record: OrderLogRecord) -> OrderLogRecord:
        self.session.add(record)
        self.session.commit()
        return record

    def update_order_log(self, order_id: str, **fields: Any) -> bool:
        record = self.session.get(OrderLogRecord, order_id)
        if not record:
            return False
        for key, value in fields.items():
            if hasattr(record, key):
                setattr(record, key, value)
        record.updated_at = datetime.now(timezone.utc)
        self.session.commit()
        return True

    def get_order_log(self, order_id: str) -> OrderLogRecord | None:
        return self.session.get(OrderLogRecord, order_id)

    def get_order_log_by_idempotency(self, key: str) -> OrderLogRecord | None:
        return self.session.query(OrderLogRecord).filter(OrderLogRecord.idempotency_key == key).first()

    def list_order_logs(self, limit: int = 200, status: str | None = None, symbol: str | None = None) -> list[OrderLogRecord]:
        query = self.session.query(OrderLogRecord).order_by(OrderLogRecord.created_at.desc())
        if status:
            query = query.filter(OrderLogRecord.status == status)
        if symbol:
            query = query.filter(OrderLogRecord.symbol == symbol)
        return query.limit(limit).all()

    def save_pattern_case(self, record: PatternCaseRecord) -> PatternCaseRecord:
        self.session.add(record)
        self.session.commit()
        return record

    def get_pattern_case(self, case_id: str) -> PatternCaseRecord | None:
        return self.session.get(PatternCaseRecord, case_id)

    def list_pattern_cases(self, symbol: str | None = None, timeframe: str | None = None, limit: int = 200) -> list[PatternCaseRecord]:
        query = self.session.query(PatternCaseRecord).order_by(PatternCaseRecord.created_at.desc())
        if symbol:
            query = query.filter(PatternCaseRecord.symbol == symbol)
        if timeframe:
            query = query.filter(PatternCaseRecord.timeframe == timeframe)
        return query.limit(limit).all()

    def delete_pattern_case(self, case_id: str) -> bool:
        record = self.session.get(PatternCaseRecord, case_id)
        if not record:
            return False
        self.session.delete(record)
        self.session.commit()
        return True

    def get_pattern_case_by_factor(self, factor_id: str) -> PatternCaseRecord | None:
        return self.session.query(PatternCaseRecord).filter(PatternCaseRecord.factor_id == factor_id).first()

    def update_pattern_statistics(self, case_id: str, statistics: dict[str, Any]) -> bool:
        record = self.session.get(PatternCaseRecord, case_id)
        if not record:
            return False
        record.statistics = statistics
        self.session.commit()
        return True

    def confirm_pattern_case(self, case_id: str, confirmed: bool = True, label: str = "") -> bool:
        record = self.session.get(PatternCaseRecord, case_id)
        if not record:
            return False
        record.human_confirmed = confirmed
        if label:
            record.pattern_type = label
        self.session.commit()
        return True

"""FastAPI 应用组装与后台任务。"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ai_engine.factor_generator import FactorGenerator
from ai_engine.ai_manager import AiManager
from ai_engine.model_pool import ModelPool
from api import ai, chat, events, factors, indicators, market, mining, models, mt5, observability, patterns, tick_archive, trading
from backtest_store.repository import FactorRepository, get_repository
from config import settings
from market_data.service import MarketDataService
from market_data.market_analysis import MarketAnalysisEngine
from observability.notifier import Notifier
from oems.mt5_broker import MT5Broker
from oems.mt5_gateway import MT5Gateway
from oems.order_manager import OrderManager
from oems.paper_broker import PaperBroker
from oems.pending_manager import PendingExpirationManager
from oems.position_monitor import MT5PositionMonitor
from oems.smart_stop import SmartStopConfig, SmartStopEngine
from oems.sltp_engine import SltpEngine
from oems.sltp_manager import SltpPolicyManager
from oems.sltp_policy import SltpPolicyConfig
from paper_trading.service import PaperTradingService
from risk_sizing.circuit_breaker import EquityRiskGuard
from signal_matcher.executor import ExecutorRuntimeConfig, SignalExecutor
from signal_matcher.matcher import MatcherService


@dataclass
class AppState:
    settings: Any = settings
    broker_override: str | None = None
    market: MarketDataService = field(init=False)
    repository: FactorRepository = field(default_factory=get_repository)
    notifier: Notifier = field(default_factory=Notifier)
    orders: OrderManager = field(init=False)
    mt5_gateway: MT5Gateway | None = field(init=False, default=None)
    pending_mgr: PendingExpirationManager | None = field(init=False, default=None)
    mt5_was_connected: bool | None = field(init=False, default=None)
    last_mt5_retry: float = 0.0
    last_matcher_scan_at: float = 0.0
    matcher_scan_interval_seconds: float = 15.0
    position_monitor: MT5PositionMonitor | None = field(init=False, default=None)
    smart_stop: SmartStopEngine | None = field(init=False, default=None)
    smart_stop_config: SmartStopConfig = field(default_factory=SmartStopConfig)
    sltp_manager: SltpPolicyManager | None = field(init=False, default=None)
    sltp_policy: SltpPolicyConfig = field(default_factory=SltpPolicyConfig)
    last_sltp_scan_at: float = 0.0
    mining_job: Any = field(default=None, init=False)
    sltp_engine: SltpEngine = field(init=False)
    last_sltp_learn_at: float = 0.0
    sltp_learn_interval_seconds: float = 300.0
    signal_executor: SignalExecutor = field(init=False)
    model_pool: ModelPool = field(default_factory=ModelPool)
    paper: PaperTradingService = field(init=False)
    matcher: MatcherService = field(init=False)
    market_analysis: MarketAnalysisEngine = field(init=False)
    ai_manager: AiManager = field(init=False)
    factor_generator: FactorGenerator = field(init=False)
    background_task: asyncio.Task | None = None
    matcher_running: bool = False
    matcher_symbol: str = settings.auto_trade_symbol
    matcher_timeframe: str = settings.auto_trade_timeframe
    matcher_pattern_min_similarity: float = settings.pattern_min_similarity
    matcher_pattern_min_samples: int = settings.pattern_min_samples
    matcher_pattern_time_decay_days: int = settings.pattern_time_decay_days
    last_scan: dict[str, Any] | None = None
    last_spread: int = 0
    last_spread_alert_at: float = 0.0
    equity_guard: EquityRiskGuard = field(default_factory=lambda: EquityRiskGuard(settings.max_daily_loss_pct, settings.max_drawdown_pct))
    risk_tripped: bool = False
    risk_tripped_warned: bool = False
    risk_reasons: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        effective_broker = self.broker_override or settings.broker
        if effective_broker == "mt5":
            mt5_broker = MT5Broker(settings)
            if settings.mt5_path and os.path.exists(settings.mt5_path):
                mt5_broker.gateway.connect()
            self.mt5_gateway = mt5_broker.gateway
            self.orders = OrderManager(mt5_broker, self.notifier)
        else:
            self.orders = OrderManager(PaperBroker(), self.notifier)
        self.market = MarketDataService(self.mt5_gateway)
        self.market_analysis = MarketAnalysisEngine(self.market)
        self.pending_mgr = PendingExpirationManager(self.mt5_gateway, self.notifier) if self.mt5_gateway else None
        self.paper = PaperTradingService(self.market, self.repository, self.orders)
        self.ai_manager = AiManager(self.repository)
        self.factor_generator = FactorGenerator(ai_manager=self.ai_manager)
        self.matcher = MatcherService(
            self.market,
            self.repository,
            self.model_pool,
            market_analysis=self.market_analysis,
            pattern_min_similarity=self.matcher_pattern_min_similarity,
            pattern_min_samples=self.matcher_pattern_min_samples,
            pattern_time_decay_days=self.matcher_pattern_time_decay_days,
        )
        self.signal_executor = SignalExecutor(
            self.market,
            self.repository,
            self.orders,
            self.notifier,
            self.mt5_gateway,
            settings,
            market_analysis=self.market_analysis,
        )
        self.position_monitor = (
            MT5PositionMonitor(self.mt5_gateway, self.repository, self.notifier) if self.mt5_gateway else None
        )
        self.smart_stop = (
            SmartStopEngine(
                self.mt5_gateway,
                self.orders,
                self.notifier,
                self.repository,
                self.ai_manager,
                self.matcher,
            )
            if self.mt5_gateway
            else None
        )
        self.sltp_engine = SltpEngine(self.repository, self.mt5_gateway, self.market)
        self.sltp_manager = (
            SltpPolicyManager(
                self.mt5_gateway,
                self.orders,
                self.notifier,
                self.repository,
                self.ai_manager,
                self.market_analysis,
            )
            if self.mt5_gateway
            else None
        )
        self.paper.trailing_config = self.signal_executor.config


async def _system_loop(state: AppState) -> None:
    while True:
        try:
            state.paper.tick()
            if state.pending_mgr:
                state.pending_mgr.check_and_clean_expired_orders()
            state.orders.sync_from_mt5()
            closed_trades: list[dict[str, Any]] = []
            if state.position_monitor:
                closed_trades = state.position_monitor.poll()
            if (
                closed_trades
                and state.sltp_engine
                and time.time() - state.last_sltp_learn_at >= state.sltp_learn_interval_seconds
            ):
                state.last_sltp_learn_at = time.time()
                try:
                    await asyncio.to_thread(state.sltp_engine.learn)
                except Exception:
                    pass
            if state.mt5_gateway and state.mt5_gateway.package_available:
                connected = state.mt5_gateway.is_connected
                if not connected and time.time() - state.last_mt5_retry > 15:
                    state.last_mt5_retry = time.time()
                    try:
                        connected = await asyncio.wait_for(
                            asyncio.to_thread(state.mt5_gateway.connect),
                            timeout=5,
                        )
                        if not connected:
                            probe = await asyncio.wait_for(
                                asyncio.to_thread(state.mt5_gateway.get_account_info),
                                timeout=5,
                            )
                            connected = bool(probe)
                    except Exception:
                        connected = False
                if state.mt5_was_connected is None:
                    state.mt5_was_connected = connected
                elif state.mt5_was_connected and not connected:
                    await state.notifier.send_alert(
                        "error",
                        "MT5 连接中断",
                        f"{state.settings.app_name} 检测到 MT5 终端连接断开，请检查终端与网络。",
                    )
                elif not state.mt5_was_connected and connected:
                    await state.notifier.send_alert("success", "MT5 连接恢复", "MT5 终端连接已恢复。")
                state.mt5_was_connected = connected
                try:
                    account = await asyncio.wait_for(
                        asyncio.to_thread(state.mt5_gateway.get_account_info),
                        timeout=8,
                    )
                except Exception:
                    account = None
                if account:
                    state.equity_guard.set_limits(
                        float(state.signal_executor.config.max_daily_loss_pct),
                        float(state.signal_executor.config.max_drawdown_pct),
                    )
                    guard_result = state.equity_guard.update(float(account["equity"]))
                    state.risk_tripped = guard_result["tripped"]
                    state.risk_reasons = guard_result["reasons"]
                    if guard_result["tripped"] and not state.risk_tripped_warned:
                        state.matcher_running = False
                        state.repository.save_system_state(
                            False,
                            state.matcher_symbol,
                            state.matcher_timeframe,
                            {
                                **state.signal_executor.config.to_dict(),
                                "pattern_min_similarity": state.matcher_pattern_min_similarity,
                                "pattern_min_samples": state.matcher_pattern_min_samples,
                                "pattern_time_decay_days": state.matcher_pattern_time_decay_days,
                            },
                        )
                        await state.notifier.send_alert(
                            "error",
                            "风控熔断",
                            "触发风控熔断：" + "；".join(guard_result["reasons"]) + "，已自动暂停实盘匹配。",
                        )
                        state.risk_tripped_warned = True
                    elif not guard_result["tripped"]:
                        state.risk_tripped_warned = False
                tick = None
                if connected:
                    try:
                        tick = await asyncio.wait_for(
                            asyncio.to_thread(state.mt5_gateway.get_tick, settings.auto_trade_symbol),
                            timeout=5,
                        )
                    except Exception:
                        tick = None
                if tick:
                    state.last_spread = int(tick.get("spread_points") or 0)
                    if (
                        state.last_spread > settings.spread_alert_threshold
                        and time.time() - state.last_spread_alert_at > settings.spread_alert_cooldown_seconds
                    ):
                        await state.notifier.send_alert(
                            "warning",
                            "点差异常",
                            f"{settings.auto_trade_symbol} 当前点差 {state.last_spread} 点，"
                            f"超过阈值 {settings.spread_alert_threshold} 点。",
                        )
                        state.last_spread_alert_at = time.time()
            if state.matcher_running:
                if state.market.is_live and time.time() - state.last_matcher_scan_at >= state.matcher_scan_interval_seconds:
                    state.last_matcher_scan_at = time.time()
                    state.last_scan = state.matcher.scan(state.matcher_symbol, state.matcher_timeframe)
                    await state.signal_executor.execute_scan(state.last_scan)
            if state.sltp_manager and state.sltp_policy.enabled:
                _mkt: dict[str, Any] = {}
                try:
                    _mkt = state.market_analysis.analyze(state.sltp_policy.symbol, state.sltp_policy.timeframe)
                except Exception:
                    _mkt = {}
                _interval = SltpPolicyManager.scan_interval(state.sltp_policy, _mkt)
                if time.time() - state.last_sltp_scan_at >= _interval:
                    state.last_sltp_scan_at = time.time()
                    try:
                        await state.sltp_manager.update(state.sltp_policy)
                    except Exception:
                        await asyncio.sleep(0)
            await asyncio.sleep(settings.market_tick_ms / 1000.0)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(2.0)


def restore_saved_matcher_state(state: AppState) -> bool:
    """后端启动时读取上次保存的运行状态，自动恢复实盘匹配。"""

    saved_sltp = state.repository.get_sltp_policy()
    if saved_sltp:
        state.sltp_policy = SltpPolicyConfig.from_dict(saved_sltp)
    saved = state.repository.get_system_state()
    if not saved:
        return False
    config_data = saved.get("config") or {}
    field_names = set(ExecutorRuntimeConfig.__dataclass_fields__)
    config = ExecutorRuntimeConfig(**{k: v for k, v in config_data.items() if k in field_names})
    state.signal_executor.apply_runtime_config(config)
    state.matcher_symbol = saved.get("symbol") or config.symbol
    state.matcher_timeframe = saved.get("timeframe") or config.timeframe
    state.matcher_pattern_min_similarity = float(
        config_data.get("pattern_min_similarity", state.matcher_pattern_min_similarity)
    )
    state.matcher_pattern_min_samples = int(config_data.get("pattern_min_samples", state.matcher_pattern_min_samples))
    state.matcher_pattern_time_decay_days = int(
        config_data.get("pattern_time_decay_days", state.matcher_pattern_time_decay_days)
    )
    state.matcher.set_pattern_config(
        min_similarity=state.matcher_pattern_min_similarity,
        min_samples=state.matcher_pattern_min_samples,
        time_decay_days=state.matcher_pattern_time_decay_days,
    )
    if not saved.get("matcher_running"):
        return False
    state.matcher_running = True
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = AppState(broker_override=getattr(app.state, "broker_override", None))
    app.state.state = state
    restored = restore_saved_matcher_state(state)
    state.background_task = asyncio.create_task(_system_loop(state))
    yield
    if state.background_task:
        state.background_task.cancel()
        try:
            await state.background_task
        except (asyncio.CancelledError, Exception):
            pass
    await state.notifier.close()
    state.repository.close()


def create_app(broker: str | None = None) -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="基于多模态 AI 逆向工程的外汇量化交易系统",
        lifespan=lifespan,
    )
    app.state.broker_override = broker
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(market.router)
    app.include_router(ai.router)
    app.include_router(chat.router)
    app.include_router(factors.router)
    app.include_router(trading.router)
    app.include_router(observability.router)
    app.include_router(mt5.router)
    app.include_router(indicators.router)
    app.include_router(models.router)
    app.include_router(patterns.router)
    app.include_router(mining.router)
    app.include_router(events.router)
    app.include_router(tick_archive.router)

    dist_dir = __import__("pathlib").Path(__file__).resolve().parents[1] / "ui_layer" / "dist"
    if dist_dir.exists():
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")
    return app

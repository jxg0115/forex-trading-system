"""全局配置：从环境变量读取，所有模块共享。"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


@dataclass(frozen=True)
class Settings:
    app_name: str = "外汇 AI 量化交易系统"
    version: str = "0.1.0"
    debug: bool = _env("DEBUG", "1") == "1"
    host: str = _env("HOST", "127.0.0.1")
    port: int = int(_env("PORT", "8000"))

    database_url: str = _env(
        "DATABASE_URL", f"sqlite:///{DATA_DIR.as_posix()}/factor_store.db"
    )
    redis_url: str = _env("REDIS_URL")

    llm_provider: str = _env("LLM_PROVIDER", "simulated")
    openai_api_key: str = _env("OPENAI_API_KEY")
    openai_model: str = _env("OPENAI_MODEL", "qwen3.8-max")
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    anthropic_model: str = _env("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")

    telegram_bot_token: str = _env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = _env("TELEGRAM_CHAT_ID")
    webhook_token: str = _env("WEBHOOK_TOKEN")   # 外部信号 webhook 鉴权 token（空 = 测试环境不鉴权）
    wechat_webhook: str = _env("WECHAT_WEBHOOK")
    dingtalk_webhook: str = _env("DINGTALK_WEBHOOK")
    feishu_webhook: str = _env("FEISHU_WEBHOOK")
    smtp_host: str = _env("SMTP_HOST")
    smtp_port: int = int(_env("SMTP_PORT", "465") or 465)
    smtp_user: str = _env("SMTP_USER")
    smtp_password: str = _env("SMTP_PASSWORD")
    alert_email_to: str = _env("ALERT_EMAIL_TO")

    broker: str = _env("BROKER", "paper")  # paper | mt5 | okx
    mt5_login: int = int(_env("MT5_LOGIN", "0") or 0)
    mt5_password: str = _env("MT5_PASSWORD")
    mt5_server: str = _env("MT5_SERVER")
    mt5_path: str = _env("MT5_PATH")
    mt5_magic: int = int(_env("MT5_MAGIC", "202608"))

    # —— OKX（欧易）配置 ——
    okx_enable: bool = _env("OKX_ENABLE", "0") == "1"
    okx_api_key: str = _env("OKX_API_KEY")
    okx_secret_key: str = _env("OKX_SECRET_KEY")
    okx_passphrase: str = _env("OKX_PASSPHRASE")
    okx_flag: str = _env("OKX_FLAG", "1")            # "1"=模拟盘（沙盒），"0"=实盘
    okx_td_mode: str = _env("OKX_TD_MODE", "cross")  # cross 全仓 / isolated 逐仓 / cash 现货
    okx_pos_side_mode: str = _env("OKX_POS_SIDE_MODE", "both")  # both 双向 / net 单向
    # 留空=自动探测 Windows 系统代理（实测本机能访问 okx 的是系统代理，非环境变量那个）；
    # 填 "direct" 表示强制直连；否则填具体代理，如 http://127.0.0.1:3067
    okx_proxy_url: str = _env("OKX_PROXY_URL")
    okx_inst_types: str = _env("OKX_INST_TYPES", "SWAP,SPOT,FUTURES")  # 可选合约类型
    # 品种归属模式：route=按 symbol 自动路由；mt5/okx=全局强制切换
    venue_mode: str = _env("VENUE_MODE", "route")

    auto_trade_enabled: bool = _env("AUTO_TRADE_ENABLED", "1") == "1"
    auto_trade_min_confidence: float = float(_env("AUTO_TRADE_MIN_CONFIDENCE", "0.55"))
    auto_trade_max_positions: int = int(_env("AUTO_TRADE_MAX_POSITIONS", "5"))
    auto_trade_risk_percent: float = float(_env("AUTO_TRADE_RISK_PERCENT", "1.0"))
    auto_trade_symbol: str = _env("AUTO_TRADE_SYMBOL", "EURUSD")
    auto_trade_timeframe: str = _env("AUTO_TRADE_TIMEFRAME", "M15")
    spread_alert_threshold: int = int(_env("SPREAD_ALERT_THRESHOLD", "50"))
    spread_alert_cooldown_seconds: int = int(_env("SPREAD_ALERT_COOLDOWN_SECONDS", "300"))
    max_drawdown_pct: float = float(_env("MAX_DRAWDOWN_PCT", "20"))
    pattern_min_similarity: float = float(_env("PATTERN_MIN_SIMILARITY", "0.85"))
    pattern_min_samples: int = int(_env("PATTERN_MIN_SAMPLES", "3"))
    pattern_time_decay_days: int = int(_env("PATTERN_TIME_DECAY_DAYS", "365"))

    account_equity: float = float(_env("ACCOUNT_EQUITY", "10000"))
    risk_per_trade_pct: float = float(_env("RISK_PER_TRADE_PCT", "1.0"))
    max_daily_loss_pct: float = float(_env("MAX_DAILY_LOSS_PCT", "3.0"))
    max_positions: int = int(_env("MAX_POSITIONS", "5"))
    leverage: int = int(_env("LEVERAGE", "30"))
    contract_size: int = int(_env("CONTRACT_SIZE", "100000"))

    market_tick_ms: int = int(_env("MARKET_TICK_MS", "3000"))
    backtest_engine: str = _env("BACKTEST_ENGINE", "custom")  # custom | vectorbt


settings = Settings()

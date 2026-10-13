"""欧易 OKX 密钥配置接口（/api/okx-settings/*）。

目的：让 OKX 的 API Key 可以在前端表单里填写，后端写回项目根目录的 `.env`
并**热重载 OKX 网关**，不用手改文件、也不用重启后端。

安全约束（务必保持）：
- 只写 `.env`，不落数据库、不进日志；GET 接口只返回掩码，绝不回传明文 Secret。
- 服务默认只监听 127.0.0.1（见 .env 的 HOST），不要把后端暴露到公网。
- 建议只勾「只读 + 交易」权限、不勾提币，并绑 IP 白名单。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends

import config as config_module
from api.deps import get_app_state
from config import settings
from market_data.symbol_registry import VENUE_OKX

router = APIRouter(prefix="/api/okx-settings", tags=["okx-settings"])

# 表单字段 -> (.env 键, Settings 字段名, 类型)
FIELDS: dict[str, tuple[str, str, str]] = {
    "enable": ("OKX_ENABLE", "okx_enable", "bool"),
    "api_key": ("OKX_API_KEY", "okx_api_key", "str"),
    "secret_key": ("OKX_SECRET_KEY", "okx_secret_key", "str"),
    "passphrase": ("OKX_PASSPHRASE", "okx_passphrase", "str"),
    "flag": ("OKX_FLAG", "okx_flag", "str"),
    "td_mode": ("OKX_TD_MODE", "okx_td_mode", "str"),
    "pos_side_mode": ("OKX_POS_SIDE_MODE", "okx_pos_side_mode", "str"),
    "proxy_url": ("OKX_PROXY_URL", "okx_proxy_url", "str"),
    "inst_types": ("OKX_INST_TYPES", "okx_inst_types", "str"),
    "venue_mode": ("VENUE_MODE", "venue_mode", "str"),
}
SECRET_FIELDS = ("api_key", "secret_key", "passphrase")


# ---------- .env 读写（必须 UTF-8：python-dotenv 按 UTF-8 严格解码） ----------

def _env_path() -> Path:
    return Path(config_module.BASE_DIR) / ".env"


def _read_env() -> tuple[bool, list[str]]:
    """返回 (是否有 UTF-8 BOM, 行列表)。"""
    path = _env_path()
    if not path.exists():
        return True, []
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    return bom, text.splitlines()


def _write_env(bom: bool, lines: list[str]) -> None:
    body = "\n".join(lines)
    if body and not body.endswith("\n"):
        body += "\n"
    _env_path().write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8"))


def _escape(value: str) -> str:
    if value == "":
        return ""
    if any(ch in value for ch in (" ", '"', "'", "#", "\n", "\t")):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _write_values(values: dict[str, str]) -> None:
    """把 keys 写进 .env：已存在的键原地替换，不存在的追加到末尾。"""
    bom, lines = _read_env()
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if not stripped.startswith("#") and key in remaining:
            out.append(f"{key}={_escape(remaining.pop(key))}")
        else:
            out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        for key, val in remaining.items():
            out.append(f"{key}={_escape(val)}")
    _write_env(bom, out)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return value[:2] + "*" * (len(value) - 2)
    return f"{value[:6]}{'*' * 8}{value[-4:]}"


# ---------- 运行时同步 ----------

def _sync_runtime(values: dict[str, str]) -> None:
    """写 .env 的同时同步 os.environ 与内存 settings。

    settings 是 frozen dataclass，这里用 object.__setattr__ 原地改，
    保证持有 settings 引用的模块（如网关构造处）能立刻看到新值。
    """
    for field, value in values.items():
        env_key, attr, kind = FIELDS[field]
        os.environ[env_key] = value
        object.__setattr__(settings, attr, value == "1" if kind == "bool" else value)


def _runtime_status(state: Any) -> dict[str, Any]:
    gw = getattr(state, "okx_gateway", None)
    return {
        "configured": gw is not None,
        "connected": bool(getattr(gw, "is_connected", False)),
        "server": ("OKX-模拟盘" if str(getattr(gw, "flag", "1")) == "1" else "OKX-实盘") if gw else "",
        "last_error": str(getattr(gw, "_last_error", "") or ""),
        "proxy": str(getattr(gw, "proxy_url", "") or "") if gw else "",
    }


def _reload_gateway(state: Any) -> dict[str, Any]:
    """按最新配置重建 OKX 网关并挂回注册表 / 下单链路。"""
    registry = getattr(state, "registry", None)
    if not settings.okx_enable:
        state.okx_gateway = None
        if registry is not None:
            registry.register(VENUE_OKX, None)
        return {"reloaded": False, "reason": "OKX 未启用（OKX_ENABLE=0）"}

    build = getattr(type(state), "_build_okx_gateway", None)
    if build is None:  # 理论上不会发生，兜底避免热重载整条链路崩掉
        return {"reloaded": False, "reason": "AppState 缺少 _build_okx_gateway"}
    gw = build()
    state.okx_gateway = gw
    if registry is not None:
        registry.register(VENUE_OKX, gw)
    # 若当前下单链路走的是 OKX，把新网关换进去（不清空已有持仓对象）
    broker = getattr(getattr(state, "orders", None), "broker", None)
    if broker is not None and type(broker).__name__ == "OkxBroker":
        broker.gateway = gw
    return {"reloaded": True, "reason": ""}


def _public_config() -> dict[str, Any]:
    return {
        "enable": settings.okx_enable,
        "api_key": _mask(settings.okx_api_key),
        "has_secret": bool(settings.okx_secret_key),
        "has_passphrase": bool(settings.okx_passphrase),
        "flag": settings.okx_flag,
        "td_mode": settings.okx_td_mode,
        "pos_side_mode": settings.okx_pos_side_mode,
        "proxy_url": settings.okx_proxy_url,
        "inst_types": settings.okx_inst_types,
        "venue_mode": settings.venue_mode,
    }


# ---------- 端点 ----------

@router.get("/config")
def get_config(state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """当前 OKX 配置（密钥只返回掩码）与网关连接状态。"""
    return {
        "success": True,
        "env_path": str(_env_path()),
        "config": _public_config(),
        "runtime": _runtime_status(state),
    }


@router.post("/config")
def save_config(payload: dict[str, Any] = Body(default={}),
                state: Any = Depends(get_app_state)) -> dict[str, Any]:
    """保存配置到 .env 并热重载网关。

    密钥字段传空串 = 保持原值不变（方便只改环境开关）；
    需要清空密钥请显式传 `clear_secrets: true`。
    """
    values: dict[str, str] = {}
    clear_secrets = bool(payload.get("clear_secrets"))

    for field, (_, _, kind) in FIELDS.items():
        if field not in payload:
            continue
        raw = payload.get(field)
        if kind == "bool":
            values[field] = "1" if raw in (True, "1", 1, "true", "True") else "0"
        else:
            values[field] = "" if raw is None else str(raw).strip()

    for field in SECRET_FIELDS:
        if field in values and values[field] == "" and not clear_secrets:
            # 空串表示"不修改"：回写成当前内存里的值
            current = {
                "api_key": settings.okx_api_key,
                "secret_key": settings.okx_secret_key,
                "passphrase": settings.okx_passphrase,
            }[field]
            values[field] = current
        elif clear_secrets and field not in values:
            values[field] = ""

    if not values:
        return {"success": False, "message": "没有需要保存的字段", "config": _public_config()}

    try:
        _write_values({FIELDS[f][0]: v for f, v in values.items()})
    except OSError as exc:
        return {"success": False, "message": f"写入 .env 失败：{exc}", "config": _public_config()}

    _sync_runtime(values)
    reload = _reload_gateway(state)
    message = "已保存并重载 OKX 网关" if reload.get("reloaded") else f"已保存（{reload.get('reason')}）"
    return {
        "success": True,
        "message": message,
        "config": _public_config(),
        "runtime": _runtime_status(state),
    }


@router.post("/test")
def test_connection(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """用「已保存的」或「本次表单里刚填的」密钥做只读连通性测试（不下单）。"""
    from oems.okx_gateway import OkxError, OkxGateway

    api_key = str(payload.get("api_key") or "").strip() or settings.okx_api_key
    secret = str(payload.get("secret_key") or "").strip() or settings.okx_secret_key
    passphrase = str(payload.get("passphrase") or "").strip() or settings.okx_passphrase
    flag = str(payload.get("flag") or settings.okx_flag or "1")
    proxy_raw = payload.get("proxy_url")
    proxy_url = (str(proxy_raw).strip() if proxy_raw is not None else settings.okx_proxy_url)
    # 与 AppState._build_okx_gateway 保持一致：空=自动探测系统代理，direct=强制直连
    if not proxy_url:
        proxy_url = None
    elif proxy_url == "direct":
        proxy_url = ""

    if not (api_key and secret and passphrase):
        return {"success": False, "message": "三件套不完整：API Key / Secret Key / Passphrase 都要填"}

    gw = OkxGateway(
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        flag=flag,
        td_mode=str(payload.get("td_mode") or settings.okx_td_mode),
        pos_side_mode=str(payload.get("pos_side_mode") or settings.okx_pos_side_mode),
        proxy_url=proxy_url,
    )
    try:
        gw._public("/api/v5/public/time")
    except OkxError as exc:
        return {"success": False, "stage": "网络", "message": f"公开接口不可达：{exc}",
                "proxy": gw.proxy_url or "（直连）"}

    acc = gw.get_account_info()
    if acc is None:
        try:
            gw._private("GET", "/api/v5/account/balance")
        except OkxError as exc:
            hint = ""
            # HTTP 非 200 时 OkxError.code 是 HTTP 状态码，业务码只在 msg 里，两个地方都要看
            blob = f"{getattr(exc, 'code', '')} {exc}"
            if any(c in blob for c in ("50111", "50113", "50119")):
                hint = "（多半是 Key 的创建环境与 OKX_FLAG 不匹配：实盘 Key 不能访问模拟盘，反之亦然）"
            elif any(c in blob for c in ("50110", "50112", "50114", "50115")):
                hint = "（IP 不在白名单，或时间戳偏差过大）"
            return {"success": False, "stage": "鉴权", "message": f"{exc}{hint}",
                    "proxy": gw.proxy_url or "（直连）"}
        return {"success": False, "stage": "鉴权", "message": "返回空账户数据"}

    return {
        "success": True,
        "stage": "完成",
        "message": f"连接成功：{acc['server']}",
        "equity": acc.get("equity"),
        "available": acc.get("free_margin"),
        "proxy": gw.proxy_url or "（直连）",
    }

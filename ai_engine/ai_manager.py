"""AI 管理服务：多配置、按板块指派、连接测试与统一多模态调用。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from backtest_store.repository import FactorRepository

# AI 可负责的系统板块（中文名与内部角色标识）
AI_ROLES: dict[str, str] = {
    "factor_learning": "K线框选学习",
    "market_monitor": "实时形态监控",
    "code_review": "因子代码审查",
    "backtest_analysis": "回测分析",
    "trade_replay": "交易复盘",
    "market_regime": "市场环境研判",
    "alert_writing": "告警文案",
    "health_diagnosis": "系统健康诊断",
    "stop_optimizer": "智能止损辅助",
    "chat_assistant": "AI 交易助手",
}

ROLE_KEYS = list(AI_ROLES)


class AiManager:
    """管理多条 AI 配置，按板块选择可用的模型并执行多模态调用。"""

    def __init__(self, repository: FactorRepository) -> None:
        self.repository = repository

    def list_configs(self) -> list[dict[str, Any]]:
        return self.repository.list_ai_configs()

    def active_for(self, role: str) -> dict[str, Any] | None:
        """返回负责指定板块且已启用的第一条配置（含明文 Key）。"""

        for cfg in self.repository.get_enabled_ai_configs():
            if role in cfg["roles"]:
                secret = self.repository.get_ai_config_secret(cfg["id"])
                if secret and secret.get("api_key"):
                    return secret
        return None

    async def test_connection(self, config_id: str) -> dict[str, Any]:
        cfg = self.repository.get_ai_config_secret(config_id)
        if not cfg:
            return {"success": False, "message": "配置不存在"}
        if not cfg.get("api_key"):
            return {"success": False, "message": "未填写 API Key"}
        started = time.time()
        try:
            raw = await asyncio.wait_for(self._chat(cfg, "请只回复：OK"), timeout=15)
            latency = round((time.time() - started) * 1000)
            return {
                "success": bool(raw and raw.strip()),
                "message": (raw or "").strip()[:120],
                "latency_ms": latency,
                "model": cfg["model"],
            }
        except Exception as exc:
            return {"success": False, "message": str(exc)[:300], "latency_ms": int((time.time() - started) * 1000)}

    async def generate(
        self,
        config_id: str,
        prompt: str,
        chart_image_data_url: str | None = None,
        system_prompt: str = "",
    ) -> str:
        cfg = self.repository.get_ai_config_secret(config_id)
        if not cfg:
            raise RuntimeError("AI 配置不存在")
        if not cfg.get("api_key"):
            raise RuntimeError("该 AI 配置未填写 API Key")
        return await self._chat(cfg, prompt, chart_image_data_url, system_prompt)

    async def _chat(
        self,
        cfg: dict[str, Any],
        prompt: str,
        chart_image_data_url: str | None = None,
        system_prompt: str = "",
    ) -> str:
        provider = cfg.get("provider", "openai")
        if provider == "anthropic":
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=cfg["api_key"], timeout=30.0)
            blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            if chart_image_data_url and "," in chart_image_data_url:
                media_type, _, base64_data = chart_image_data_url.partition(",")
                blocks.insert(
                    0,
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type.replace("data:", "").replace(";base64", ""),
                            "data": base64_data,
                        },
                    },
                )
            response = await asyncio.wait_for(
                client.messages.create(
                    model=cfg.get("model", "claude-3-5-sonnet-latest"),
                    max_tokens=4000,
                    system=system_prompt or "你是一名专业的量化交易工程师，所有输出使用简体中文。",
                    messages=[{"role": "user", "content": blocks}],
                ),
                timeout=45,
            )
            return "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text")

        from openai import AsyncOpenAI

        base_url = cfg.get("base_url") or None
        client = AsyncOpenAI(api_key=cfg["api_key"], base_url=base_url, timeout=30.0)
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if chart_image_data_url:
            content.append({"type": "image_url", "image_url": {"url": chart_image_data_url}})
        request_kwargs: dict[str, Any] = {
            "model": cfg.get("model", "qwen3.8-max"),
            "messages": [
                {"role": "system", "content": system_prompt or "你是一名专业的量化交易工程师，所有输出使用简体中文。"},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
            "max_tokens": 2000,
        }
        if (base_url and "dashscope" in base_url) or str(cfg.get("model", "")).startswith("qwen"):
            request_kwargs["extra_body"] = {"enable_thinking": False}
        response = await asyncio.wait_for(
            client.chat.completions.create(**request_kwargs),
            timeout=60,
        )
        return response.choices[0].message.content or ""

    def status(self) -> dict[str, Any]:
        configs = self.list_configs()
        enabled = [c for c in configs if c["enabled"]]
        roles_assigned: dict[str, list[str]] = {}
        for role, label in AI_ROLES.items():
            assigned = [c["name"] for c in enabled if role in c["roles"]]
            roles_assigned[role] = {"label": label, "configs": assigned}
        return {
            "configured": len(configs) > 0,
            "enabled_count": len(enabled),
            "configs": configs,
            "roles": roles_assigned,
        }

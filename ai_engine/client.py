"""OpenAI / Anthropic 多模态客户端。"""

from __future__ import annotations

import asyncio
from typing import Any

from config import settings
from ai_engine.prompts import SYSTEM_PROMPT


class LLMClient:
    """按环境变量自动选择 OpenAI 或 Anthropic；无 Key 时返回 simulated。"""

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self._openai = None
        self._anthropic = None
        if self.provider in {"openai", "auto"} and settings.openai_api_key:
            try:
                from openai import AsyncOpenAI

                self._openai = AsyncOpenAI(api_key=settings.openai_api_key, timeout=30.0)
                self.provider = "openai"
            except ImportError:
                self._openai = None
        if self.provider in {"anthropic", "auto"} and settings.anthropic_api_key:
            try:
                from anthropic import AsyncAnthropic

                self._anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=30.0)
                self.provider = "anthropic"
            except ImportError:
                self._anthropic = None
        if self.provider == "auto" and self._openai is None and self._anthropic is None:
            self.provider = "simulated"

    async def generate(
        self,
        prompt: str,
        chart_image_data_url: str | None = None,
    ) -> str:
        """返回大模型原始输出；无可用 Key 时抛出 RuntimeError。"""

        if self.provider == "openai" and self._openai is not None:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            if chart_image_data_url:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": chart_image_data_url},
                    }
                )
            request_kwargs: dict[str, Any] = {
                "model": settings.openai_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                "temperature": 0.2,
                "max_tokens": 2000,
            }
            if str(settings.openai_model).startswith("qwen"):
                request_kwargs["extra_body"] = {"enable_thinking": False}
            response = await asyncio.wait_for(
                self._openai.chat.completions.create(**request_kwargs),
                timeout=60,
            )
            return response.choices[0].message.content or ""

        if self.provider == "anthropic" and self._anthropic is not None:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            if chart_image_data_url and "," in chart_image_data_url:
                media_type, _, base64_data = chart_image_data_url.partition(",")
                mime = media_type.replace("data:", "").replace(";base64", "")
                blocks.insert(
                    0,
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64_data,
                        },
                    },
                )
            response = await asyncio.wait_for(
                self._anthropic.messages.create(
                    model=settings.anthropic_model,
                    max_tokens=2000,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": blocks}],
                ),
                timeout=45,
            )
            parts = [b.text for b in response.content if getattr(b, "type", "") == "text"]
            return "\n".join(parts)

        raise RuntimeError("未配置可用的 LLM API Key")

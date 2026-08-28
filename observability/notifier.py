"""告警中心：入库、Telegram 推送（可选）、内存最近告警。"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx

from backtest_store.repository import AlertRecord, FactorRepository, get_repository
from config import settings
from models.factor import AlertModel


class Notifier:
    def __init__(self, repository: FactorRepository | None = None) -> None:
        self.repository = repository
        self._memory: list[AlertModel] = []
        self._client = httpx.AsyncClient(timeout=8.0)

    async def send_alert(self, level: str, title: str, message: str) -> AlertModel:
        alert = AlertModel(
            id=str(uuid.uuid4()),
            level=level,  # type: ignore[arg-type]
            title=title,
            message=message,
            created_at=datetime.now(timezone.utc),
            delivered_to=[],
        )
        self._memory.insert(0, alert)
        self._memory = self._memory[:200]

        if settings.telegram_bot_token and settings.telegram_chat_id:
            try:
                await self._send_telegram(title, message)
                alert.delivered_to.append("telegram")
            except Exception:
                alert.delivered_to.append("telegram 失败")

        if settings.wechat_webhook:
            try:
                await self._send_wechat(title, message)
                alert.delivered_to.append("wechat")
            except Exception:
                alert.delivered_to.append("企业微信 失败")

        if settings.dingtalk_webhook:
            try:
                await self._send_dingtalk(title, message)
                alert.delivered_to.append("dingtalk")
            except Exception:
                alert.delivered_to.append("钉钉 失败")

        if settings.feishu_webhook:
            try:
                await self._send_feishu(title, message)
                alert.delivered_to.append("feishu")
            except Exception:
                alert.delivered_to.append("飞书 失败")

        if settings.smtp_host and settings.alert_email_to:
            try:
                await self._send_email(title, message)
                alert.delivered_to.append("email")
            except Exception:
                alert.delivered_to.append("邮件 失败")

        try:
            repo = self.repository or get_repository()
            repo.save_alert(
                AlertRecord(
                    id=alert.id,
                    level=alert.level,
                    title=alert.title,
                    message=alert.message,
                    delivered_to=alert.delivered_to,
                )
            )
            if self.repository is None:
                repo.close()
        except Exception:
            pass
        return alert

    async def _send_telegram(self, title: str, message: str) -> None:
        text = f"【{title}】\n{message}"
        await self._client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_chat_id, "text": text},
        )

    async def _send_wechat(self, title: str, message: str) -> None:
        text = f"【{title}】\n{message}"
        await self._client.post(
            settings.wechat_webhook,
            json={"msgtype": "text", "text": {"content": text}},
        )

    async def _send_dingtalk(self, title: str, message: str) -> None:
        text = f"【{title}】\n{message}"
        await self._client.post(
            settings.dingtalk_webhook,
            json={"msgtype": "text", "text": {"content": text}},
        )

    async def _send_feishu(self, title: str, message: str) -> None:
        text = f"【{title}】\n{message}"
        await self._client.post(
            settings.feishu_webhook,
            json={"msg_type": "text", "content": {"text": text}},
        )

    async def _send_email(self, title: str, message: str) -> None:
        await asyncio.to_thread(self._send_email_sync, title, message)

    def _send_email_sync(self, title: str, message: str) -> None:
        mail = EmailMessage()
        mail["Subject"] = f"【外汇AI交易系统】{title}"
        mail["From"] = settings.smtp_user
        mail["To"] = settings.alert_email_to
        mail.set_content(f"【{title}】\n{message}")
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context, timeout=8) as server:
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(mail)

    def recent(self, limit: int = 50) -> list[AlertModel]:
        return self._memory[:limit]

    async def close(self) -> None:
        await self._client.aclose()

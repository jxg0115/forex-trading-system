"""板块九：多渠道告警 payload 测试（不触达真实网络）。"""

import asyncio
from types import SimpleNamespace

import observability.notifier as notifier_module
from observability.notifier import Notifier


class FakeRepo:
    def save_alert(self, alert):
        return alert


class FakeClient:
    def __init__(self):
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, json=None, timeout=None):
        self.posts.append((url, json or {}))
        return SimpleNamespace(status_code=200)


BASE_SETTINGS = {
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "wechat_webhook": "",
    "dingtalk_webhook": "",
    "feishu_webhook": "",
    "smtp_host": "",
    "smtp_port": 465,
    "smtp_user": "",
    "smtp_password": "",
    "alert_email_to": "",
}


def _send(monkeypatch, overrides):
    merged = {**BASE_SETTINGS, **overrides}
    monkeypatch.setattr(notifier_module, "settings", SimpleNamespace(**merged))
    notifier = Notifier(repository=FakeRepo())
    client = FakeClient()
    notifier._client = client

    async def run():
        return await notifier.send_alert("info", "测试告警", "告警内容")

    return client, asyncio.run(run())


def test_telegram_payload(monkeypatch):
    client, alert = _send(monkeypatch, {"telegram_bot_token": "tok", "telegram_chat_id": "123"})
    assert "telegram" in alert.delivered_to
    url, payload = client.posts[0]
    assert "api.telegram.org" in url
    assert payload["chat_id"] == "123"
    assert "测试告警" in payload["text"]


def test_wechat_payload(monkeypatch):
    client, alert = _send(monkeypatch, {"wechat_webhook": "https://qyapi.weixin.qq.com/robot"})
    assert "wechat" in alert.delivered_to
    url, payload = client.posts[0]
    assert payload["msgtype"] == "text"
    assert "测试告警" in payload["text"]["content"]


def test_dingtalk_payload(monkeypatch):
    client, alert = _send(monkeypatch, {"dingtalk_webhook": "https://oapi.dingtalk.com/robot"})
    assert "dingtalk" in alert.delivered_to
    url, payload = client.posts[0]
    assert payload["msgtype"] == "text"


def test_feishu_payload(monkeypatch):
    client, alert = _send(monkeypatch, {"feishu_webhook": "https://open.feishu.cn/open-apis/bot"})
    assert "feishu" in alert.delivered_to
    url, payload = client.posts[0]
    assert payload["msg_type"] == "text"

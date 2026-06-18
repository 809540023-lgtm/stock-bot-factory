import base64
import hashlib
import hmac
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class LineWebhookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["INVESTMENT_PLAN_JSON_PATH"] = os.path.join(self.tmp.name, "plans.json")
        os.environ["LINE_CHANNEL_SECRET"] = "test-secret"
        os.environ["LINE_CHANNEL_ACCESS_TOKEN"] = "test-token"

        import app
        self.app_module = importlib.reload(app)
        self.client = self.app_module.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def post_webhook(self, payload):
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return self.client.post(
            "/investment-plans/line-webhook",
            data=body,
            content_type="application/json",
            headers={"X-Line-Signature": _signature("test-secret", body)},
        )

    def test_rejects_invalid_signature(self):
        resp = self.client.post(
            "/investment-plans/line-webhook",
            data=b'{"events":[]}',
            content_type="application/json",
            headers={"X-Line-Signature": "bad"},
        )

        self.assertEqual(resp.status_code, 403)

    def test_follow_event_prompts_binding(self):
        payload = {
            "destination": "bot",
            "events": [{
                "type": "follow",
                "replyToken": "reply-token",
                "source": {"type": "user", "userId": "Uline-user"},
            }],
        }

        with patch.object(self.app_module, "_line_reply", return_value=True) as reply:
            resp = self.post_webhook(payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["handled"][0]["status"], "prompted_binding")
        self.assertIn("綁定", reply.call_args.args[1])

    def test_text_binding_creates_active_subscription(self):
        payload = {
            "destination": "bot",
            "events": [{
                "type": "message",
                "replyToken": "reply-token",
                "source": {"type": "user", "userId": "Uline-user"},
                "message": {"type": "text", "id": "1", "text": "綁定 guest"},
            }],
        }

        with patch.object(self.app_module, "_line_reply", return_value=True):
            resp = self.post_webhook(payload)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["handled"][0]["status"], "bound")
        subs = self.app_module.PLAN_STORE.list_line_subscriptions("guest")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].line_user_id, "Uline-user")
        self.assertEqual(subs[0].status, "active")


if __name__ == "__main__":
    unittest.main()

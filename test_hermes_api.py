"""Offline regression check: Hermes routes must authenticate and reach call control."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from sales_policy import Campaign


class HermesAPITest(unittest.TestCase):
    def request(self, method, path, payload=None, secret="test-secret"):
        handler = object.__new__(server.Handler)
        body = json.dumps(payload or {}).encode()
        handler.path = path
        handler.headers = {"X-AI-Helper-Secret": secret, "Content-Type": "application/json", "Content-Length": str(len(body))}
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        getattr(handler, "do_" + method)()
        return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue())

    def test_hermes_call_flow(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "DB_PATH", Path(directory) / "test.db"), patch.dict(server.os.environ, {"INTERNAL_AI_HELPER_SECRET": "test-secret"}):
            server.init_db()
            status, _ = self.request("GET", "/internal/ai-helper/leads", secret="wrong")
            self.assertEqual(status, 401)
            status, lead = self.request("POST", "/internal/ai-helper/leads", {"business_name": "Example", "phone": "+12025550123", "contact_name": "Owner", "notes": "Verified business authorization to contact.", "metadata": {"authorized_to_call": True, "callback_at": 0}})
            self.assertEqual(status, 201)
            meta = lead["metadata_json"]
            meta["priority"] = {"source": "jev", "band": "high", "readiness_score": 90, "assessed_at": server.time.time(), "evidence_fingerprint": server.lead_priority.fingerprint(lead, {})}
            server.ai_helper_update_lead(lead["id"], {"metadata_json": meta})
            with patch.object(server, "outbound_call", return_value={"sid": "test-call", "status": "queued"}) as dial:
                status, _ = self.request("POST", f"/internal/ai-helper/leads/{lead['id']}/call")
                self.assertEqual(status, 201)
                dial.assert_called_once_with("+12025550123")
            self.assertEqual(server.load_call("test-call")["contact_name"], "Owner")
            status, result = self.request("GET", f"/internal/ai-helper/leads/{lead['id']}/calls")
            self.assertEqual(status, 200)
            self.assertEqual(result["calls"][0]["call_sid"], "test-call")
            server.save_recording("test-call", "https://example.com/recording.mp3", "10")
            status, result = self.request("GET", "/internal/ai-helper/calls/test-call")
            self.assertEqual(status, 200)
            self.assertEqual(result["recording_duration"], 10)
            self.assertEqual(result["recording_url"], "https://example.com/recording.mp3")
            self.assertTrue(result["transcript"])
            status, payment = self.request("POST", f"/internal/ai-helper/leads/{lead['id']}/payments", {"amount": "100"})
            self.assertEqual(status, 201)
            status, result = self.request("POST", f"/internal/ai-helper/payments/{payment['id']}/mark-paid")
            self.assertEqual((status, result["status"]), (200, "payment_received"))
            status, _ = self.request("GET", "/internal/ai-helper/calls/test-call", secret="wrong")
            self.assertEqual(status, 401)
            for prompt in (server.agent_system(), Campaign.from_env("AI Helper").monthly_price):
                self.assertIn("$200 USD", prompt)
                self.assertIn("$500 USD", prompt)

    def test_unauthorized_lead_cannot_be_dialed(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "DB_PATH", Path(directory) / "test.db"), patch.dict(server.os.environ, {"INTERNAL_AI_HELPER_SECRET": "test-secret"}):
            server.init_db()
            _, lead = self.request("POST", "/internal/ai-helper/leads", {"business_name": "No Consent", "phone": "+12025550123"})
            with patch.object(server, "outbound_call") as dial:
                status, result = self.request("POST", f"/internal/ai-helper/leads/{lead['id']}/call")
            self.assertEqual(status, 400)
            self.assertIn("authorized", result["error"])
            dial.assert_not_called()


if __name__ == "__main__":
    unittest.main()

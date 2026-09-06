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
            status, lead = self.request("POST", "/internal/ai-helper/leads", {"business_name": "Example", "phone": "+12025550123", "contact_name": "Owner"})
            self.assertEqual(status, 201)
            with patch.object(server, "outbound_call", return_value={"sid": "test-call"}) as dial:
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
            status, _ = self.request("GET", "/internal/ai-helper/calls/test-call", secret="wrong")
            self.assertEqual(status, 401)
            for prompt in (server.agent_system(), Campaign.from_env("AI Helper").monthly_price):
                self.assertIn("₹20,000", prompt)
                self.assertIn("$100/month", prompt)
                self.assertIn("$200/month", prompt)


if __name__ == "__main__":
    unittest.main()

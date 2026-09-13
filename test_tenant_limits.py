import asyncio
import io
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import server
import tenant_limits as limits


class BusinessLimitsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_patch = patch.object(server, "DB_PATH", Path(self.directory.name) / "test.db")
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.scope = limits.CURRENT.set(None)
        self.reservation = limits.RESERVATION.set(None)
        self.addCleanup(limits.CURRENT.reset, self.scope)
        self.addCleanup(limits.RESERVATION.reset, self.reservation)
        server.init_db()
        for i, slug in enumerate(("alpha", "beta")):
            server.create_company(slug, "test-password")
            config = {"provider": "twilio", "plan": "usd_100", "period": "paid-1", "ends_at": int(time.time()) + 86400,
                      "keys": {name: slug + "-" + name for name in ("GROQ_API_KEY", "RUMIK_API_KEY", "ASSEMBLYAI_API_KEY", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")}}
            config["keys"]["TWILIO_PHONE_NUMBER"] = f"+1202555012{i}"
            with server.db() as conn:
                limits.configure(conn, slug, config)

    def reserve(self, slug, identifier):
        with server.db() as conn:
            return limits.reserve(conn, server.business_scope(slug), identifier)

    def test_quota_concurrency_idempotence_and_renewal(self):
        with server.db() as conn:
            conn.execute("INSERT INTO call_reservations(id,slug,period,sid,seconds,charged) VALUES ('old','alpha','paid-1','old',59995,59995)")
        def attempt(identifier):
            try:
                return self.reserve("alpha", identifier)["seconds"]
            except ValueError:
                return "blocked"
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(attempt, ("one", "two")))
        self.assertCountEqual(results, [5, "blocked"])
        with server.db() as conn:
            row = conn.execute("SELECT id FROM call_reservations WHERE charged IS NULL").fetchone()
            limits.bind(conn, row["id"], "CAfinal")
            limits.settle(conn, "CAfinal", "5")
            limits.settle(conn, "CAfinal", "5")
            self.assertTrue(limits.usage(conn, server.business_scope("alpha"))["paused"])
        with self.assertRaises(ValueError):
            self.reserve("alpha", "three")
        self.assertEqual(self.reserve("beta", "other")["seconds"], 600)
        with server.db() as conn:
            limits.configure(conn, "alpha", {"period": "paid-2", "ends_at": int(time.time()) + 172800})
        with server.db() as conn:
            self.assertEqual(limits.usage(conn, server.business_scope("alpha"))["remaining_seconds"], 60000)

    def test_credentials_remain_isolated_across_async_tasks_and_threads(self):
        async def read(slug):
            token = limits.CURRENT.set(server.business_scope(slug))
            try:
                await asyncio.sleep(0)
                return await asyncio.to_thread(server.setting, "GROQ_API_KEY")
            finally:
                limits.CURRENT.reset(token)
        async def run():
            return await asyncio.gather(read("alpha"), read("beta"))
        self.assertEqual(asyncio.run(run()), ["alpha-GROQ_API_KEY", "beta-GROQ_API_KEY"])
        limits.CURRENT.set(server.business_scope("alpha"))
        with patch.dict(server.os.environ, {"SARVAM_API_KEY": "shared-secret"}), self.assertRaises(RuntimeError):
            server.setting("SARVAM_API_KEY")
        self.assertNotIn("beta", server.agent_system())
        server.create_company("unconfigured", "password")
        with self.assertRaises(ValueError):
            server.business_outbound("+12025550999", "unconfigured")

    def test_provider_receives_limit_and_signed_reservation_url(self):
        with patch.object(server, "twilio_request", return_value={"sid": "CAtest", "status": "queued"}) as request:
            call = server.business_outbound("+12025550999", "alpha", "Help with booking")
        body = request.call_args.args[1]
        self.assertEqual(body["From"], "+12025550120")
        self.assertEqual(body["TimeLimit"], 600)
        self.assertIn("reservation=" + call["reservation_id"], body["Url"])
        self.assertEqual(server.load_call("CAtest")["company_slug"], "alpha")
        self.assertIsNone(limits.CURRENT.get())
        with patch.object(server, "outbound_call", side_effect=TimeoutError), self.assertRaises(TimeoutError):
            server.business_outbound("+12025550999", "beta")
        with self.assertRaises(ValueError):
            self.reserve("beta", "retry")

    def test_binding_and_configuration_cannot_cross_businesses(self):
        self.reserve("alpha", "one")
        server.start_call("foreign", "inbound", "", company_slug="beta")
        with server.db() as conn:
            with self.assertRaises(ValueError):
                limits.bind(conn, "one", "foreign")
            with self.assertRaises(ValueError):
                limits.configure(conn, "alpha", {"period": "next"})
            with self.assertRaises(ValueError):
                limits.settle(conn, "foreign", "nan")
        with server.db() as conn:
            config = server.business_scope("alpha")
            with self.assertRaises(ValueError):
                limits.configure(conn, "beta", {"keys": config["keys"]})

    def test_webhook_uses_business_key_and_settles_once(self):
        self.reserve("alpha", "one")
        server.start_call("one", "outbound", "+12025550999", company_slug="alpha")
        params = {"CallSid": "CAfinal", "CallStatus": "completed", "CallDuration": "10"}
        sid = server.webhook_business("/twilio/status?reservation=one", params, "twilio")
        self.assertEqual(server.setting("TWILIO_AUTH_TOKEN"), "alpha-TWILIO_AUTH_TOKEN")
        server.bind_business_call("one", sid)
        server.settle_business_call(sid, params, "twilio")
        server.settle_business_call(sid, params, "twilio")
        with server.db() as conn:
            self.assertEqual(limits.usage(conn, server.business_scope("alpha"))["used_seconds"], 10)

    def test_timeout_terminates_provider_without_media_and_restores_context(self):
        row = self.reserve("alpha", "one")
        server.start_call("CAwait", "inbound", "", company_slug="alpha")
        with server.db() as conn:
            limits.bind(conn, "one", "CAwait")
            conn.execute("UPDATE call_reservations SET answered_at=? WHERE id='one'", (time.time() - row["seconds"],))
        socket = Mock(close=AsyncMock())
        async def wait_forever(*args, **kwargs):
            await asyncio.Future()
        with patch.object(server, "_run_live_call", side_effect=wait_forever), patch.object(server, "terminate_call") as end:
            asyncio.run(server.run_live_call(socket, {"start": {"callSid": "CAwait"}}))
        end.assert_called_once_with("CAwait", "twilio", "duration_limit")
        self.assertIsNone(limits.CURRENT.get())

    def test_repetition_warns_then_stops_but_short_answers_are_allowed(self):
        messages = [{"role": "user", "content": "Please repeat that entire story again"}] * 3
        self.assertEqual(limits.repetition(messages), "warn")
        self.assertEqual(limits.repetition(messages + messages[:1]), "repeated_request")
        self.assertEqual(limits.repetition([{"role": "user", "content": "yes"}] * 8), "")

    def test_signed_webhooks_cannot_charge_or_answer_another_business(self):
        self.reserve("alpha", "one")
        server.start_call("one", "outbound", "+12025550999", company_slug="alpha")
        path = "/twilio/status?reservation=one"
        params = {"CallSid": "CAend", "CallStatus": "completed", "CallDuration": "10"}
        body = server.urllib.parse.urlencode(params).encode()
        def request(secret):
            handler = object.__new__(server.Handler)
            handler.path = path
            signed = server.public_url(path) + "".join(k + params[k] for k in sorted(params))
            signature = server.base64.b64encode(server.hmac.new(secret.encode(), signed.encode(), server.hashlib.sha1).digest()).decode()
            handler.headers = {"Content-Type": "application/x-www-form-urlencoded", "Content-Length": str(len(body)), "X-Twilio-Signature": signature}
            handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
            handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
            handler.do_POST()
            return handler.send_response.call_args.args[0]
        self.assertEqual(request("beta-TWILIO_AUTH_TOKEN"), 403)
        with server.db() as conn:
            self.assertIsNone(conn.execute("SELECT charged FROM call_reservations WHERE id='one'").fetchone()[0])
        self.assertEqual(request("alpha-TWILIO_AUTH_TOKEN"), 200)
        self.assertIsNone(limits.CURRENT.get())
        with server.db() as conn:
            self.assertEqual(conn.execute("SELECT charged FROM call_reservations WHERE id='one'").fetchone()[0], 10)

    def test_plivo_request_id_and_signature_query_handling(self):
        config = server.business_scope("alpha")
        keys = {k: v for k, v in config["keys"].items() if not k.startswith("TWILIO_")}
        keys.update(PLIVO_AUTH_ID="alpha-plivo", PLIVO_AUTH_TOKEN="plivo-token", PLIVO_PHONE_NUMBER="+12025550120")
        with server.db() as conn:
            limits.configure(conn, "alpha", {"provider": "plivo", "keys": keys})
        with patch.object(server, "plivo_request", return_value={"request_uuid": "request-not-call"}) as request:
            call = server.business_outbound("+12025550999", "alpha")
        self.assertEqual(request.call_args.args[1]["time_limit"], 600)
        with server.db() as conn:
            self.assertIsNone(conn.execute("SELECT sid FROM call_reservations WHERE id=?", (call["reservation_id"],)).fetchone()[0])
        path = "/plivo/answer?reservation=" + call["reservation_id"]
        params = {"CallUUID": "actual-call"}
        server.webhook_business(path, params, "plivo")
        signed = server.public_url(path) + ".CallUUIDactual-call.nonce"
        signature = server.base64.b64encode(server.hmac.new(b"plivo-token", signed.encode(), server.hashlib.sha256).digest()).decode()
        self.assertTrue(server.verify_plivo(path, params, signature, "nonce"))
        self.assertFalse(server.verify_plivo(path, {"CallUUID": "different"}, signature, "nonce"))
        self.assertFalse(server.verify_plivo(path, params, signature, "nonce", v3=False))
        self.assertEqual(server.admit_business_call("actual-call"), 600)
        self.assertEqual(server.load_call("actual-call")["company_slug"], "alpha")

    def test_static_handler_blocks_secrets_and_traversal_even_for_head(self):
        for path in ("/aihelper.db", "/.env", "/tenant_limits.py", "/auth/better-auth.sqlite", "/%2e%2e/.hermes/.env"):
            handler = object.__new__(server.Handler)
            handler.path = path
            handler.send_error = Mock()
            self.assertIsNone(handler.send_head())
            handler.send_error.assert_called_once_with(404)

    def test_recording_prefetch_caches_while_url_fresh(self):
        seen = []

        class SyncThread:
            def __init__(self, target, daemon=None, name=None):
                seen.append(target)

            def start(self):
                seen[-1]()

        with patch.object(server.threading, "Thread", SyncThread), patch.object(server, "cached_recording", return_value=(b"x", "audio/mpeg")) as cached:
            server.prefetch_recording("CA1", "https://example.com/r.mp3")
        cached.assert_called_once_with("CA1", "https://example.com/r.mp3")
        server.prefetch_recording("", "")
        server.prefetch_recording("CA1", "")

    def test_expired_telnyx_recording_refreshes_and_plays(self):
        import urllib.error

        class FakeResp:
            def __init__(self, payload):
                self.payload = payload
                self.headers = {"Content-Type": "audio/mpeg"}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return self.payload

        expired = urllib.error.HTTPError("https://old", 403, "expired", {}, io.BytesIO())
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "RECORDINGS_DIR", Path(directory)):
            with patch("urllib.request.urlopen", side_effect=[expired, FakeResp(b"fresh-audio")]), patch.object(server, "refresh_telnyx_recording", return_value="https://fresh") as refresh:
                payload, ctype = server.cached_recording("v3:expired", "https://old")
            self.assertEqual((payload, ctype), (b"fresh-audio", "audio/mpeg"))
            refresh.assert_called_once_with("v3:expired")
            self.assertTrue((Path(directory) / "v3:expired.mp3").exists())

    def test_company_recording_plays_for_own_call_only(self):
        server.start_call("CArec", "outbound", "+12025550999", "ctx", company_slug="alpha")
        with server.db() as conn:
            conn.execute("UPDATE calls SET recording_url=? WHERE sid='CArec'", ("https://example.com/r.mp3",))

        def get(path):
            handler = object.__new__(server.Handler)
            handler.path = path
            handler.headers = {}
            handler.rfile, handler.wfile = io.BytesIO(b""), io.BytesIO()
            handler.send_response, handler.send_header, handler.end_headers, handler.send_error = Mock(), Mock(), Mock(), Mock()
            with patch.object(server.Handler, "require_company", return_value=True), patch.object(server, "cached_recording", return_value=(b"0123456789", "audio/mpeg")):
                handler.do_GET()
            return handler

        own = get("/company/alpha/calls/CArec/recording")
        own.send_response.assert_called_with(200)
        self.assertEqual(own.wfile.getvalue(), b"0123456789")

        foreign = get("/company/beta/calls/CArec/recording")
        foreign.send_error.assert_called_with(404)
        foreign.send_response.assert_not_called()

        missing = get("/company/alpha/calls/NOPE/recording")
        missing.send_error.assert_called_with(404)
        missing.send_response.assert_not_called()

    def test_unlimited_still_has_call_limits_and_expired_periods_stop(self):
        with server.db() as conn:
            limits.configure(conn, "alpha", {"plan": "usd_200", "period": "unlimited-paid"})
        self.assertEqual(self.reserve("alpha", "one")["seconds"], 600)
        with server.db() as conn:
            self.assertIsNone(limits.usage(conn, server.business_scope("alpha"))["remaining_seconds"])
        with patch.object(limits.time, "time", return_value=time.time() + 172800), self.assertRaises(ValueError):
            self.reserve("beta", "expired")


if __name__ == "__main__":
    unittest.main()

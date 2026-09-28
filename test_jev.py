import copy
import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

import jev


MESSAGES = [{"role": "user", "content": "How does this help our café?"}]


def reply(tone="happy", event="chuckle", action="explain_solution", stage="opening"):
    answers = {
        name: {"type": "choice", "choice": choice, "confidence": 0.9,
               "probabilities": {key: float(key == choice) for key in jev.QUESTIONS[name]["criteria"]}}
        for name, choice in zip(("tone", "event", "action"), (tone, event, action))
    }
    allowed = (stage, *jev.STAGE_EDGES[stage])
    answers["stage"] = {"type": "choice", "choice": stage, "confidence": 0.9,
                        "probabilities": {key: float(key == stage) for key in allowed}}
    scope = {key: float(key == "unclear") for key in ("outbound", "inbound", "both", "unclear")}
    answers["call_scope"] = {"type": "choice", "choice": "unclear", "confidence": 0.9,
                             "probabilities": scope}
    return {"answers": answers}


class JevTests(unittest.TestCase):
    def setUp(self):
        self.patch = patch("jev.build_opener")
        self.builder = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.open = self.builder.return_value.open
        self.response = self.open.return_value.__enter__.return_value
        self.response.status = 200
        self.response.read.return_value = json.dumps(reply()).encode()

    def test_batched_official_contract_and_tags(self):
        messages = [{"role": "system", "content": "secret"},
                    {"role": "user", "content": "Private hidden context", "_private": True}, *MESSAGES]
        original = copy.deepcopy(messages)
        result = jev.decide(messages, "key", timeout=0.25)
        self.assertEqual(result, {"source": "jev", "tone": "happy", "event": "chuckle",
                                 "action": "explain_solution", "tone_tag": "[happy]",
                                 "event_tag": "<chuckle>", "stage": "opening", "call_scope": "unclear"})
        self.open.assert_called_once()
        request = self.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/alpha/decisions")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer key")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(self.open.call_args.kwargs, {"timeout": 0.25})
        body = json.loads(request.data)
        self.assertEqual(body["state"], MESSAGES)
        self.assertEqual(body["model"], "~typesafe/jev-latest")
        self.assertEqual(set(body["questions"]), {"tone", "event", "action", "stage", "call_scope"})
        self.assertEqual(set(body["questions"]["stage"]["criteria"]), {
            "opening", "call_scope", "nurture", "end_call"})
        self.assertEqual(set(body["questions"]["call_scope"]["criteria"]), {
            "outbound", "inbound", "both", "unclear"})
        self.assertEqual(set(body["questions"]["action"]["criteria"]), {
            "discover", "qualify", "answer_question", "explain_solution", "discuss_price",
            "confirm_email", "arrange_next_step", "opt_out", "end_call", "human_handoff", "clarify"})
        self.assertEqual(messages, original)

    def test_stage_routes_are_limited_to_current_exits(self):
        self.response.read.return_value = json.dumps(reply(stage="call_scope")).encode()
        result = jev.decide(MESSAGES, "key", current_stage="call_scope", call_direction="inbound")
        self.assertEqual(result["stage"], "call_scope")
        body = json.loads(self.open.call_args.args[0].data)
        self.assertEqual(set(body["questions"]["stage"]["criteria"]), {"call_scope", "discovery"})
        self.assertIn("inbound", body["questions"]["stage"]["instructions"])
        self.response.read.return_value = json.dumps(reply(stage="end_call")).encode()
        self.assertEqual(jev.decide(MESSAGES, "key", current_stage="call_scope")["source"], "fallback")

    def test_graph_route_definitions_are_complete_and_stage_is_typed(self):
        self.assertEqual(set(jev.ROUTES), set(jev.STAGE_EDGES))
        for stage, exits in jev.STAGE_EDGES.items():
            self.assertEqual(set(jev.ROUTES[stage]), set(exits))
        body = reply()
        body["answers"]["call_scope"]["choice"] = "maybe"
        self.response.read.return_value = json.dumps(body).encode()
        self.assertEqual(jev.decide(MESSAGES, "key")["source"], "fallback")

    def test_missing_key_and_invalid_input_never_connect(self):
        for key in ("", "  ", None):
            self.assertEqual(jev.decide(MESSAGES, key)["source"], "fallback")
        for messages in (None, "hello", [], [None], [{"role": "user", "content": 7}]):
            self.assertEqual(jev.decide(messages, "key")["source"], "fallback")
        for timeout in (0, -1, None, True, float("nan"), float("inf")):
            self.assertEqual(jev.decide(MESSAGES, "key", timeout)["source"], "fallback")
        self.builder.assert_not_called()

    def test_outages_do_not_retry(self):
        errors = [TimeoutError(), URLError("offline"), OSError("TLS failure")]
        errors += [HTTPError(jev.URL, code, "failure", {}, None) for code in (401, 429, 500, 529)]
        for error in errors:
            with self.subTest(error=error):
                self.open.reset_mock()
                self.open.side_effect = error
                self.assertEqual(jev.decide(MESSAGES, "key"), jev._result())
                self.open.assert_called_once()
                self.assertEqual(self.open.call_args.kwargs["timeout"], 0.6)

    def test_invalid_and_low_confidence_responses(self):
        bodies = [b"not json", b"null", b"[]", b"{}", b"x" * 65537]
        mutations = [("choice", "angry"), ("choice", []), ("type", "score"),
                     ("confidence", 0.79), ("confidence", True), ("confidence", "0.9"),
                     ("confidence", -1), ("confidence", 2), ("confidence", float("nan")),
                     ("confidence", float("inf")), ("probabilities", {}),
                     ("probabilities", {"happy": 1})]
        for name in jev.QUESTIONS:
            for field, value in mutations:
                body = reply()
                body["answers"][name][field] = value
                bodies.append(json.dumps(body).encode())
            body = reply()
            del body["answers"][name]
            bodies.append(json.dumps(body).encode())
        for raw in bodies:
            with self.subTest(raw=raw[:120]):
                self.response.read.return_value = raw
                self.assertEqual(jev.decide(MESSAGES, "key"), jev._result())

    def test_probability_validation(self):
        for value in (True, "1", -1, 2, float("nan"), float("inf"), 0.4):
            body = reply()
            body["answers"]["tone"]["probabilities"]["happy"] = value
            self.response.read.return_value = json.dumps(body).encode()
            self.assertEqual(jev.decide(MESSAGES, "key"), jev._result())
        body = reply()
        body["answers"]["tone"]["probabilities"].update(happy=0.1, sad=0.9)
        self.response.read.return_value = json.dumps(body).encode()
        self.assertEqual(jev.decide(MESSAGES, "key"), jev._result())

    def test_all_tone_event_pairs_and_actions(self):
        for tone in ("neutral", "happy", "sad", "excited"):
            for event in ("none", "chuckle", "laugh", "sigh"):
                self.response.read.return_value = json.dumps(reply(tone, event)).encode()
                result = jev.decide(MESSAGES, "key")
                allowed = (event == "none" or (event == "sigh" and tone in ("neutral", "sad"))
                           or (event in ("chuckle", "laugh") and tone in ("happy", "excited")))
                self.assertEqual(result["tone"], tone)
                self.assertEqual(result["event"], event if allowed else "none")
        for action in jev.QUESTIONS["action"]["criteria"]:
            self.response.read.return_value = json.dumps(reply(action=action)).encode()
            result = jev.decide(MESSAGES, "key")
            self.assertEqual(result["action"], "end_call" if action == "opt_out" else action)
            if action in ("opt_out", "end_call", "human_handoff"):
                self.assertEqual((result["tone"], result["event"]), ("neutral", "none"))

    def test_redirect_is_rejected_without_followup(self):
        # Exercise urllib's actual redirect handler without any socket access.
        handler = jev._NoRedirect()
        handler.parent = MagicMock()
        self.assertIsNone(handler.http_error_302(
            jev.Request(jev.URL), io.BytesIO(), 302, "Moved",
            {"location": "https://example.com/steal"}))
        handler.parent.open.assert_not_called()
        self.assertIsInstance(jev._NoRedirect(), jev.HTTPRedirectHandler)

    def test_non_success_and_read_failure(self):
        self.response.status = 302
        self.assertEqual(jev.decide(MESSAGES, "key"), jev._result())
        self.response.status = 200
        self.response.read.side_effect = TimeoutError()
        self.assertEqual(jev.decide(MESSAGES, "key"), jev._result())


if __name__ == "__main__":
    unittest.main()

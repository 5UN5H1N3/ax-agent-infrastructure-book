from copy import deepcopy
from datetime import timedelta
import io
import subprocess
import unittest
from unittest.mock import patch

import ch36_mcp_stdio as m


class MCPFixtureTests(unittest.TestCase):
    def call(self, **arguments):
        return m.request("r", "tools/call", name=m.TOOL["name"], arguments={**m.ARGS, **arguments})

    def response(self):
        return m.dispatch(self.call())

    def serve(self, raw):
        output = io.BytesIO()
        status = m.serve(io.BytesIO(raw), output)
        return status, [m.decode(row) for row in output.getvalue().splitlines()]

    def test_discovery(self):
        result = m.dispatch(m.request(1, "server/discover"))["result"]
        self.assertEqual(result["supportedVersions"], [m.VERSION])
        self.assertEqual(result["capabilities"], {"tools": {}})

    def test_catalog_publishes_contract(self):
        result = m.dispatch(m.request(1, "tools/list"))["result"]
        self.assertEqual(result["tools"], [m.TOOL])
        result["tools"][0]["name"] = "mutated"
        self.assertEqual(m.TOOL["name"], "network.read_route")

    def test_call_without_prior_discovery(self):
        self.assertEqual(m.validate_observation(self.response(), "r", m.ARGS), m.OBSERVATION)

    def test_version_mismatch(self):
        req = self.call(); req["params"]["_meta"][m.PREFIX + "protocolVersion"] = "2025-11-25"
        err = m.dispatch(req)["error"]
        self.assertEqual(err["code"], -32022)
        self.assertEqual(err["data"], {"supported": [m.VERSION], "requested": "2025-11-25"})

    def test_missing_metadata(self):
        req = self.call(); del req["params"]["_meta"]
        self.assertEqual(m.dispatch(req)["error"]["code"], -32602)

    def test_capabilities_checked_each_request(self):
        for value in [None, [], "", False]:
            with self.subTest(value=value):
                req = self.call(); req["params"]["_meta"][m.PREFIX + "clientCapabilities"] = value
                self.assertEqual(m.dispatch(req)["error"]["code"], -32602)

    def test_invalid_request_ids(self):
        for value in [None, True, 1.2, {}, []]:
            req = self.call(); req["id"] = value
            self.assertEqual(m.dispatch(req)["error"]["code"], -32600)

    def test_notification_produces_no_response_or_action(self):
        req = self.call(); del req["id"]
        self.assertIsNone(m.dispatch(req))

    def test_unknown_method(self):
        self.assertEqual(m.dispatch(m.request(1, "resources/list"))["error"]["code"], -32601)

    def test_initialize_not_supported(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        self.assertEqual(m.dispatch(req)["error"]["code"], -32602)

    def test_unknown_write(self):
        req = self.call(); req["params"]["name"] = "network.apply_config"
        self.assertEqual(m.dispatch(req)["error"]["code"], -32602)

    def test_bad_envelope_arguments(self):
        req = self.call(); req["params"]["arguments"] = []
        self.assertEqual(m.dispatch(req)["error"]["code"], -32602)

    def test_extra_argument_is_tool_error(self):
        result = m.dispatch(self.call(tenant="admin"))["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["content"][0]["text"], "invalid_arguments")

    def test_missing_argument_is_tool_error(self):
        req = self.call(); del req["params"]["arguments"]["vrf"]
        self.assertTrue(m.dispatch(req)["result"]["isError"])

    def test_argument_type(self):
        self.assertEqual(m.dispatch(self.call(prefix=17))["result"]["content"][0]["text"], "invalid_arguments")

    def test_cidr_semantics(self):
        for prefix in ["no-network", "192.0.2.1/24"]:
            self.assertEqual(m.dispatch(self.call(prefix=prefix))["result"]["content"][0]["text"], "invalid_prefix")

    def test_fixed_target_scope(self):
        for change in [{"device": "production-edge-1"}, {"vrf": "OTHER"}, {"prefix": "198.51.100.0/24"}]:
            self.assertEqual(m.dispatch(self.call(**change))["result"]["content"][0]["text"], "outside_fixture_scope")

    def test_client_info_is_not_authority(self):
        req = self.call(device="production-edge-1")
        req["params"]["_meta"][m.PREFIX + "clientInfo"] = {"name": "admin", "version": "root"}
        self.assertTrue(m.dispatch(req)["result"]["isError"])

    def test_result_detached_from_fixture(self):
        self.response()["result"]["structuredContent"]["device"] = "changed"
        self.assertEqual(m.OBSERVATION["device"], "lab-edge-1")

    def test_id_mismatch(self):
        with self.assertRaisesRegex(ValueError, "id_mismatch"):
            m.validate_observation(self.response(), "different", m.ARGS)

    def test_id_type_not_coerced(self):
        reply = self.response(); reply["id"] = True
        with self.assertRaisesRegex(ValueError, "id_mismatch"):
            m.validate_response(reply, 1)

    def test_result_and_error_not_both(self):
        reply = self.response(); reply["error"] = {"code": -32602, "message": "bad"}
        with self.assertRaisesRegex(ValueError, "ambiguous_response"):
            m.validate_response(reply, "r")

    def test_unhandled_variant_not_success(self):
        for variant in ["input_required", "task", "new-kind", None]:
            reply = self.response(); reply["result"]["resultType"] = variant
            with self.assertRaisesRegex(ValueError, "unsupported_result_type"):
                m.validate_response(reply, "r")

    def test_tool_error_not_observation(self):
        with self.assertRaisesRegex(ValueError, "tool_error"):
            m.validate_observation(m.dispatch(self.call(prefix="bad")), "r", m.ARGS)

    def test_rpc_error_not_observation(self):
        with self.assertRaisesRegex(ValueError, "rpc_error"):
            m.validate_observation(m.error("r", -32602, "bad"), "r", m.ARGS)

    def test_wrong_target_not_observation(self):
        reply = self.response(); reply["result"]["structuredContent"]["device"] = "other"
        with self.assertRaisesRegex(ValueError, "wrong_object"):
            m.validate_observation(reply, "r", m.ARGS)

    def test_missing_output_field(self):
        reply = self.response(); del reply["result"]["structuredContent"]["raw_ref"]
        with self.assertRaisesRegex(ValueError, "invalid_output"):
            m.validate_observation(reply, "r", m.ARGS)

    def test_output_boolean_strict(self):
        reply = self.response(); reply["result"]["structuredContent"]["present"] = 1
        with self.assertRaisesRegex(ValueError, "invalid_output_type"):
            m.validate_observation(reply, "r", m.ARGS)

    def test_output_integer_not_bool(self):
        reply = self.response(); reply["result"]["structuredContent"]["local_pref"] = True
        with self.assertRaisesRegex(ValueError, "invalid_output_type"):
            m.validate_observation(reply, "r", m.ARGS)

    def test_stale_and_future_observation(self):
        for dt in [timedelta(seconds=61), timedelta(seconds=-31)]:
            with self.assertRaisesRegex(ValueError, "stale_or_future"):
                m.validate_observation(self.response(), "r", m.ARGS, now=m.NOW + dt)

    def test_naive_timestamp(self):
        reply = self.response(); reply["result"]["structuredContent"]["observed_at"] = "2026-10-17T07:00:00"
        with self.assertRaisesRegex(ValueError, "invalid_timestamp"):
            m.validate_observation(reply, "r", m.ARGS)

    def test_fixture_provenance_checked(self):
        reply = self.response(); reply["result"]["structuredContent"]["source"] = "router"
        with self.assertRaisesRegex(ValueError, "wrong_fixture_provenance"):
            m.validate_observation(reply, "r", m.ARGS)

    def test_three_messages_one_process(self):
        replies, diagnostic = m.exchange([m.request("d", "server/discover"), m.request("l", "tools/list"), self.call()])
        self.assertEqual([x["id"] for x in replies], ["d", "l", "r"])
        self.assertIn(b"fixed offline fixture", diagnostic)

    def test_restart_read_same_fixture(self):
        first, _ = m.exchange([self.call()]); second, _ = m.exchange([self.call()])
        self.assertEqual(first, second)

    def test_duplicate_ids_before_launch(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            m.exchange([self.call(), self.call()])

    def test_timeout_kills_and_reaps_child(self):
        with patch.object(m.subprocess, "Popen") as factory:
            child = factory.return_value
            child.communicate.side_effect = [subprocess.TimeoutExpired("fixture", 3), (b"", b"")]
            with self.assertRaises(TimeoutError):
                m.exchange([self.call()])
            child.kill.assert_called_once()
            self.assertEqual(child.communicate.call_count, 2)

    def test_framing_unicode_and_escaped_newline(self):
        raw = m.line({"text": "строка\nвторая"})
        self.assertEqual(raw.count(b"\n"), 1)
        self.assertEqual(m.decode(raw)["text"], "строка\nвторая")

    def test_invalid_json_and_utf8(self):
        for raw in [b"not-json\n", b"\xff\n", b"NaN\n"]:
            self.assertEqual(self.serve(raw)[1][0]["error"]["code"], -32700)

    def test_oversized_frame_closes_fixture(self):
        self.assertEqual(self.serve(b"x" * (m.LIMIT + 1) + b"\n"), (2, []))

    def test_missing_newline_rejected(self):
        self.assertEqual(self.serve(m.line(self.call()).rstrip(b"\n")), (2, []))

    def test_notification_on_wire_has_no_reply(self):
        req = self.call(); del req["id"]
        self.assertEqual(self.serve(m.line(req)), (0, []))

    def test_demo(self):
        result = m.demo()
        self.assertEqual(result["config_revision"], "config-18")
        self.assertEqual(result["operation_outcome"], "OUTCOME_UNKNOWN")
        self.assertEqual(result["model_calls"] + result["network_calls"], 0)


if __name__ == "__main__":
    unittest.main()

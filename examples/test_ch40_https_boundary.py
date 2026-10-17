import json
import ssl
import unittest

import ch40_https_boundary as m


class HTTPSBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lab = m.Lab().__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.lab.__exit__(None, None, None)

    def setUp(self):
        self.lab.configure()

    def stages(self, result):
        return [e["stage"] for e in result["events"]]

    def test_health_is_not_application_authorization(self):
        health = self.lab.probe("/health")
        probe = self.lab.probe()
        self.assertEqual(health["status"], 200)
        self.assertEqual(probe["status"], 401)
        self.assertEqual(probe["emitter"], "origin")
        self.assertTrue(probe["origin_received"])

    def test_allowed_probe_reaches_origin(self):
        self.lab.configure(inject_token=m.TEST_TOKEN)
        r = self.lab.probe()
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["marker"], "probe_ok")
        self.assertEqual(r["client_tls"], "VERIFIED")
        self.assertEqual(self.stages(r), ["policy_allow", "http_received", "origin_http"])

    def test_denied_endpoint_is_alive(self):
        r = self.lab.probe("/health", direct_canary=True)
        self.assertEqual(r["status"], 200)
        self.assertTrue(r["canary_received"])

    def test_denied_host_is_policy_denial_not_tls_failure(self):
        r = self.lab.probe("/health", authority=m.DENIED)
        self.assertEqual(r["client_tls"], "VERIFIED")
        self.assertEqual(r["status"], 403)
        self.assertEqual(r["emitter"], "gateway")
        self.assertEqual(self.stages(r), ["policy_deny"])
        self.assertFalse(r["origin_received"])
        self.assertFalse(r["canary_received"])

    def test_control_allow_canary_proves_route_is_usable(self):
        self.lab.configure(allow_hosts=(m.MODELS, m.DENIED))
        r = self.lab.probe("/health", authority=m.DENIED)
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["emitter"], "canary")
        self.assertTrue(r["canary_received"])

    def test_client_wrong_ca_stops_before_http_policy(self):
        r = self.lab.probe(cafile=self.lab.origin_ca)
        self.assertEqual(r["client_tls"], "REJECTED")
        self.assertIsNone(r["status"])
        self.assertEqual(r["events"], [])

    def test_upstream_wrong_ca_is_separate_failure(self):
        self.lab.configure(upstream_trust=self.lab.gateway_ca)
        r = self.lab.probe()
        self.assertEqual(r["client_tls"], "VERIFIED")
        self.assertEqual(r["status"], 502)
        self.assertEqual(r["emitter"], "gateway")
        self.assertEqual(self.stages(r), ["policy_allow", "upstream_tls_failed"])
        self.assertFalse(r["origin_received"])

    def test_wrong_peer_name_rejected_even_with_correct_ca(self):
        r = self.lab.probe(server_name="wrong.example.test")
        self.assertEqual(r["client_tls"], "REJECTED")
        self.assertEqual(r["events"], [])

    def test_allowed_sni_does_not_authorize_denied_authority(self):
        r = self.lab.probe("/health", authority=m.DENIED, server_name=m.MODELS)
        self.assertEqual(r["status"], 403)
        self.assertFalse(r["canary_received"])

    def test_allowed_authority_does_not_authorize_denied_sni(self):
        r = self.lab.probe("/health", authority=m.MODELS, server_name=m.DENIED)
        self.assertEqual(r["client_tls"], "VERIFIED")
        self.assertEqual(r["status"], 403)
        self.assertEqual(self.stages(r), ["policy_deny"])

    def test_wrong_credential_is_origin_auth_failure(self):
        self.lab.configure(inject_token="wrong-fixture-token")
        r = self.lab.probe()
        self.assertEqual(r["status"], 401)
        self.assertEqual(r["emitter"], "origin")
        self.assertTrue(r["origin_received"])

    def test_missing_path_is_not_policy_denial(self):
        r = self.lab.probe("/missing")
        self.assertEqual(r["status"], 404)
        self.assertEqual(r["emitter"], "origin")

    def test_client_and_upstream_use_distinct_roots(self):
        self.assertNotEqual(self.lab.gateway_ca.read_bytes(), self.lab.origin_ca.read_bytes())

    def test_verification_never_disabled(self):
        for ca in [self.lab.gateway_ca, self.lab.origin_ca]:
            context = m.client_context(ca)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)

    def test_listener_addresses_are_loopback(self):
        self.assertEqual([s.server_address[0] for s in self.lab.servers], ["127.0.0.1"] * 3)

    def test_routes_cannot_be_extended_to_external_names(self):
        with self.assertRaises(ValueError):
            self.lab.configure(allow_hosts=("external.example.test",))

    def test_gateway_refuses_unknown_authority_without_dial(self):
        r = self.lab.probe(authority="external.example.test", server_name=m.MODELS)
        self.assertEqual(r["status"], 403)
        self.assertEqual(self.stages(r), ["policy_deny"])

    def test_request_ids_isolate_negative_evidence(self):
        self.lab.probe("/health")
        denied = self.lab.probe("/health", authority=m.DENIED)
        self.assertEqual(self.stages(denied), ["policy_deny"])
        self.assertFalse(denied["origin_received"])

    def test_public_evidence_contains_no_token_or_private_key(self):
        self.lab.configure(inject_token=m.TEST_TOKEN)
        data = json.dumps(self.lab.probe())
        self.assertNotIn(m.TEST_TOKEN, data)
        self.assertNotIn("PRIVATE KEY", data)
        self.assertNotIn("Authorization", data)

    def test_probe_never_changes_policy(self):
        before = self.lab.allow_hosts
        r = self.lab.probe("/health", authority=m.DENIED)
        self.assertEqual(r["automatic_policy_changes"], 0)
        self.assertEqual(self.lab.allow_hosts, before)

    def test_default_credential_absence_does_not_fail_closed_at_gateway(self):
        r = self.lab.probe()
        self.assertEqual(self.stages(r)[0], "policy_allow")
        self.assertEqual(r["status"], 401)
        self.assertTrue(r["origin_received"])

    def test_demo(self):
        r = m.demo()
        self.assertEqual(r["network_scope"], "LOOPBACK_ONLY")
        self.assertFalse(r["substrate_executed"])
        self.assertEqual(r["external_requests"], 0)
        self.assertEqual(r["cases"]["allowed"]["marker"], "probe_ok")
        self.assertEqual(r["cases"]["denied"]["emitter"], "gateway")


if __name__ == "__main__":
    unittest.main()

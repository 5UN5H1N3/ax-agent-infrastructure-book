"""Policy selection tests only; they cannot establish runtime containment."""
import ast
from dataclasses import replace
from pathlib import Path
import re
import unittest

from ch08_isolation_policy_demo import PROFILES, PolicyError, Profile, Request, select_profile


class PolicyTests(unittest.TestCase):
    def reject(self, **changes):
        with self.assertRaises(PolicyError):
            select_profile(replace(Request(), **changes), PROFILES)

    def test_baseline(self):
        self.assertEqual(select_profile(Request(), PROFILES), {
            "status": "plan_only", "input_revision": "demo-fixture-v1",
            "profile": "review-gvisor-offline-v1", "boundary": "system-api",
            "enforcement_verified": False,
        })

    def test_guest_kernel(self):
        plan = select_profile(Request(needs_guest_kernel=True), PROFILES)
        self.assertEqual(plan["boundary"], "guest-kernel")
        self.assertEqual(plan["profile"], "review-microvm-offline-v1")

    def test_empty_catalog(self):
        with self.assertRaises(PolicyError):
            select_profile(Request(), ())

    def test_no_downgrade(self):
        with self.assertRaises(PolicyError):
            select_profile(Request(needs_guest_kernel=True), PROFILES[:1])

    def test_no_automatic_upgrade(self):
        with self.assertRaises(PolicyError):
            select_profile(Request(), PROFILES[1:])

    def test_shared_kernel_not_fallback(self):
        container = Profile("container", "shared-kernel", frozenset({"python-demo"}))
        with self.assertRaises(PolicyError):
            select_profile(Request(), (container,))

    def test_incompatible_catalog(self):
        other = Profile("other", "system-api", frozenset({"rust-demo"}))
        with self.assertRaises(PolicyError):
            select_profile(Request(), (other,))

    def test_ambiguous_catalog(self):
        with self.assertRaises(PolicyError):
            select_profile(Request(), (PROFILES[0], PROFILES[0]))

    def test_no_catalog_order_preference(self):
        self.assertEqual(select_profile(Request(), PROFILES), select_profile(Request(), tuple(reversed(PROFILES))))

    def test_request_not_mutated(self):
        request = Request()
        before = repr(request)
        select_profile(request, PROFILES)
        self.assertEqual(repr(request), before)

    def test_unknown_trust(self):
        self.reject(code_trust="unknown")

    def test_self_declared_trusted_not_admitted(self):
        self.reject(code_trust="trusted")

    def test_other_execution(self):
        self.reject(execution="shell")

    def test_unknown_toolchain(self):
        self.reject(toolchain="rust-demo")

    def test_network(self):
        self.reject(network="internet")

    def test_runtime_socket(self):
        self.reject(capabilities=("runtime-socket",))

    def test_host_mount(self):
        self.reject(capabilities=("host-root",))

    def test_gpu(self):
        self.reject(capabilities=("gpu",))

    def test_production_secret(self):
        self.reject(capabilities=("prod-secrets",))

    def test_wrong_capability_type(self):
        self.reject(capabilities=[])

    def test_wrong_boolean_type(self):
        for value in ("false", 0, 1, None):
            with self.subTest(value=value):
                self.reject(needs_guest_kernel=value)

    def test_wrong_revision_type(self):
        for value in (None, 123, b"demo"):
            with self.subTest(value=value):
                self.reject(input_revision=value)

    def test_invalid_revision_label(self):
        for value in ("", "a" * 129, "../source", "bad label", "name\n", "ревизия"):
            with self.subTest(value=value):
                self.reject(input_revision=value)

    def test_revision_boundaries(self):
        for value in ("x", "a" * 128):
            with self.subTest(value=value):
                self.assertEqual(select_profile(Request(input_revision=value), PROFILES)["input_revision"], value)

    def test_wrong_request_type(self):
        for value in (None, {}, "Request"):
            with self.subTest(value=value), self.assertRaises(PolicyError):
                select_profile(value, PROFILES)

    def test_wrong_string_types(self):
        for field in ("code_trust", "execution", "toolchain", "network"):
            with self.subTest(field=field):
                self.reject(**{field: None})

    def test_plan_never_claims_enforcement(self):
        for guest in (False, True):
            plan = select_profile(Request(needs_guest_kernel=guest), PROFILES)
            self.assertIs(plan["enforcement_verified"], False)
            self.assertEqual(plan["status"], "plan_only")

    def test_chapter_excerpt_matches_function(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        chapter = re.search(r'<section\b[^>]*\bid="ch-8"[^>]*>.*?</section>', html, re.S)[0]
        blocks = re.findall(r'<code class="language-python">(.*?)</code>', chapter, re.S)
        self.assertEqual(len(blocks), 1)
        from html import unescape
        printed = ast.parse(unescape(blocks[0])).body[0]
        actual = next(node for node in ast.parse((root / "examples/ch08_isolation_policy_demo.py").read_text()).body
                      if isinstance(node, ast.FunctionDef) and node.name == "select_profile")
        self.assertEqual(ast.dump(printed), ast.dump(actual))


if __name__ == "__main__":
    unittest.main()

"""Offline policy-only tests: no QQ, DNS resolution, sockets, listeners or namespaces."""

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest

from tools.qq_egress_policy import validate_resolved_destination as validate


PUBLIC_V4 = "8.8.8.8"
PUBLIC_V6 = "2001:4860:4860::8888"
# Cover conservative explicit ranges, including older ipaddress table gaps.
DENIED = (
    "0.0.0.0", "0.1.2.3", "10.0.0.1", "100.64.0.0", "100.127.255.255",
    "127.0.0.1", "169.254.169.254", "172.16.0.0", "172.31.255.255",
    "192.0.0.9", "192.0.0.10", "192.0.2.1", "192.88.99.1", "192.168.1.1",
    "198.18.0.0", "198.19.255.255", "198.51.100.1", "203.0.113.1",
    "224.0.0.1", "239.255.255.255", "240.0.0.1", "255.255.255.255",
    "::", "::1", "::8.8.8.8", "::ffff:8.8.8.8", "::ffff:127.0.0.1",
    "64:ff9b::808:808", "64:ff9b:1::808:808", "100::1",
    "2001::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2", "2001:2::1",
    "2001:10::1", "2001:20::1", "2001:1ff::1", "2001:db8::1",
    "2002:808:808::1", "2002:7f00:1::1", "3fff::1", "3fff:fff::1",
    "fc00::1", "fd00::1", "fe80::1", "fec0::1", "ff02::1", "4000::1",
)
INVALID = (
    None, True, 134744072, b"8.8.8.8", "", " 8.8.8.8", "8.8.8.8\n",
    "008.008.008.008", "010.0.0.1", "127.1", "2130706433", "0x7f000001",
    "0x7f.0.0.1", "8.8.8.8/32", "8.8.8.256", "[2001:4860:4860::8888]",
    "2001:4860:4860::8888%eth0", "2001:4860:4860::8888%1", "example.com",
)


class EgressPolicyTests(unittest.TestCase):
    """Exercise the public API only, keeping every case deterministic and offline."""

    def test_public_literals_and_ports(self):
        """Permit both families and the full explicit integer port range by default."""
        for address in (PUBLIC_V4, PUBLIC_V6):
            for port in (1, 443, 65535):
                with self.subTest(address=address, port=port):
                    plan = validate(address, port, [address])
                    self.assertEqual((plan.host, plan.port, plan.addresses),
                                     (address, port, (address,)))

    def test_canonical_stable_dedup_and_frozen(self):
        """Pin canonical IPv6 answers without changing answer preference or mutability."""
        answers = [PUBLIC_V4, "2001:4860:4860:0000:0000:0000:0000:8888", PUBLIC_V4,
                   PUBLIC_V6]
        plan = validate("ExAmPlE.COM", 443, answers, {443})
        self.assertEqual(plan.host, "example.com")
        self.assertEqual(plan.addresses, (PUBLIC_V4, PUBLIC_V6))
        answers.append("127.0.0.1")
        self.assertEqual(len(plan.addresses), 2)
        with self.assertRaises(FrozenInstanceError):
            plan.port = 80
        with self.assertRaises(TypeError):
            plan.addresses[0] = "127.0.0.1"

    def test_denied_ranges_fail_everywhere(self):
        """Reject special literals and mixed DNS sets, not merely filter bad answers."""
        for address in DENIED:
            with self.subTest(address=address):
                for host, answers in ((address, [address]),
                                      ("example.com", [PUBLIC_V4, address]),
                                      ("example.com", [address, PUBLIC_V6])):
                    with self.assertRaises(ValueError):
                        validate(host, 443, answers)

    def test_invalid_candidate_types_and_spellings(self):
        """Require actual literal text and reject zone identifiers or coercion."""
        for address in INVALID:
            with self.subTest(address=address), self.assertRaises(ValueError):
                validate("example.com", 443, [address])

    def test_host_syntax_and_disguised_ips(self):
        """Forbid credentials, URLs, numeric aliases, whitespace and lossy IDNA."""
        hosts = (None, True, 8, b"example.com", "", "user@example.com", "https://example.com",
                 "example.com/path", "example.com:443", "example.com?x", "example.com#x",
                 "example.com.", ".example.com", "example..com", "-a.com", "a-.com",
                 "a_b.com", " example.com", "example.com\n", "a" * 64 + ".com",
                 "a." * 127 + "com", "127.1", "2130706433", "0177.0.0.1", "0x7f000001",
                 "0x7f.0x0.0x0.0x1", "999.999.999.999", "８.８.８.８", "example。com",
                 "faß.de", "xn--.com", "xn--abc.com", "[8.8.8.8]", "8.8.8.8%1")
        for host in hosts:
            with self.subTest(host=host), self.assertRaises(ValueError):
                validate(host, 443, [PUBLIC_V4])

    def test_idna_and_case(self):
        """Accept canonical reversible international names without doing resolution."""
        for host in ("bücher.example", "BÜCHER.EXAMPLE", "xn--bcher-kva.example"):
            self.assertEqual(validate(host, 443, [PUBLIC_V4]).host, "xn--bcher-kva.example")

    def test_literal_mismatch(self):
        """A public answer must not replace the explicitly requested literal host."""
        for answers in (["1.1.1.1"], [PUBLIC_V4, PUBLIC_V6]):
            with self.assertRaises(ValueError):
                validate(PUBLIC_V4, 443, answers)
        expanded = "2001:4860:4860:0000:0000:0000:0000:8888"
        self.assertEqual(validate(expanded, 443, [PUBLIC_V6]).host, PUBLIC_V6)

    def test_ports_exact_types_and_allowed_set(self):
        """Reject bool and coercible ports including invalid entries in allow sets."""
        for port in (True, False, 0, -1, 65536, 443.0, "443", None):
            with self.subTest(port=port), self.assertRaises(ValueError):
                validate("example.com", port, [PUBLIC_V4])
        for allowed in (set(), {80}, {443, True}, {443, 65536}, {443, "80"}, [443], (443,)):
            with self.subTest(allowed=allowed), self.assertRaises(ValueError):
                validate("example.com", 443, [PUBLIC_V4], allowed)
        self.assertEqual(validate("example.com", 443, [PUBLIC_V4], frozenset({443})).port, 443)

    def test_answer_bounds_precede_dedup(self):
        """Reject empty, oversized, lazy or unordered answers before processing them."""
        for answers in ([], (), None, PUBLIC_V4, {PUBLIC_V4}, {"address": PUBLIC_V4},
                        iter([PUBLIC_V4]), [PUBLIC_V4] * 17):
            with self.subTest(kind=type(answers).__name__), self.assertRaises(ValueError):
                validate("example.com", 443, answers)
        self.assertEqual(validate("example.com", 443, [PUBLIC_V4] * 16).addresses,
                         (PUBLIC_V4,))

    def test_explicit_range_edges(self):
        """Check adjacent public values so conservative deny bounds do not expand silently."""
        for address in ("100.63.255.255", "100.128.0.0", "172.15.255.255", "172.32.0.0",
                        "198.17.255.255", "198.20.0.0", "2001:200::1", "3ffe::1"):
            with self.subTest(address=address):
                self.assertEqual(validate(address, 443, [address]).addresses, (address,))

    def test_source_syntax_and_offline_imports(self):
        """Parse changed Python files and ensure the policy has no network/process imports."""
        root = Path(__file__).resolve().parents[1]
        source = root / "tools" / "qq_egress_policy.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imports = {node.module if isinstance(node, ast.ImportFrom) else alias.name
                   for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names}
        self.assertEqual(imports, {"dataclasses", "ipaddress", "re"})


if __name__ == "__main__":
    unittest.main()

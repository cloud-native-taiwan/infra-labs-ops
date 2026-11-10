"""Runs ansible/files/cert-renewal.sh against stub binaries (no certbot, kolla,
Harbor, or systemd touched).

The three cert consumers (edge HAProxy, Harbor, the kolla fleet) are
independent: one failing must not skip the others, but the script must still
exit non-zero and leave the deployed-stamp unwritten so the next timer tick
retries and the cert_renewal success metric is not written.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ANSIBLE_DIR / "files/cert-renewal.sh"

STUB = """#!/bin/sh
echo "{name} $*" >> "$STUB_LOG"
{body}
exit "${{STUB_RC_{name}:-0}}"
"""
OPENSSL_BODY = """case "$*" in
  *-fingerprint*) echo "sha256 Fingerprint=${STUB_FP:-AA:BB}" ;;
  *-ext*) echo "    DNS:*.cloudnative.tw, DNS:cloudnative.tw" ;;
esac"""


class CertRenewalScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.bin = root / "bin"
        self.live = root / "live"
        self.kolla_certs = root / "kolla"
        self.edge = root / "edge"
        self.harbor = root / "harbor"
        for d in (self.bin, self.live, self.kolla_certs, self.edge, self.harbor):
            d.mkdir()
        (self.live / "fullchain.pem").write_text("CERT\n")
        (self.live / "privkey.pem").write_text("KEY\n")
        self.log = root / "calls.log"
        self.log.touch()
        for name in ("certbot", "openssl", "chown", "runuser", "systemctl", "docker"):
            self._stub(self.bin / name, name, OPENSSL_BODY if name == "openssl" else "")
        self._stub(self.harbor / "prepare", "prepare", "")
        self.stamp = self.kolla_certs / "haproxy.pem.deployed"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def _stub(path: Path, name: str, body: str) -> None:
        path.write_text(STUB.format(name=name, body=body))
        path.chmod(0o755)

    def _run(self, **rcs: int) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "KOLLA_USER": "deployer",
            "LIVE_DIR": str(self.live),
            "HAPROXY_PEM": str(self.kolla_certs / "haproxy.pem"),
            "EDGE_PEM": str(self.edge / "cloudnative.tw.pem"),
            "HARBOR_DIR": str(self.harbor),
            **{f"STUB_RC_{name}": str(rc) for name, rc in rcs.items()},
        }
        return subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True
        )

    def _calls(self) -> list[str]:
        return [line.split()[0] for line in self.log.read_text().splitlines()]

    def test_all_steps_succeed_records_stamp(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        for name in ("systemctl", "prepare", "docker", "runuser"):
            self.assertIn(name, calls)
        self.assertEqual(self.stamp.read_text().strip(), "AA:BB")
        self.assertEqual((self.edge / "cloudnative.tw.pem").read_text(), "CERT\nKEY\n")

    def test_harbor_failure_still_pushes_kolla_but_fails(self) -> None:
        result = self._run(prepare=1)
        self.assertNotEqual(result.returncode, 0)
        calls = self._calls()
        self.assertIn("runuser", calls, "Harbor failure skipped the kolla push")
        self.assertNotIn("docker", calls, "compose ran after ./prepare failed")
        self.assertFalse(self.stamp.exists())
        self.assertIn("harbor", result.stderr)

    def test_edge_reload_failure_still_runs_harbor_and_kolla(self) -> None:
        result = self._run(systemctl=1)
        self.assertNotEqual(result.returncode, 0)
        calls = self._calls()
        self.assertIn("prepare", calls)
        self.assertIn("runuser", calls)
        self.assertFalse(self.stamp.exists())

    def test_edge_write_failure_skips_reload_only(self) -> None:
        # A missing edge cert dir makes the bundle write fail; the reload in
        # the same step must not run on a half-done write (set -e does not
        # apply inside a step run under `if !`).
        self.edge.rmdir()
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        calls = self._calls()
        self.assertNotIn("systemctl", calls)
        self.assertIn("prepare", calls)
        self.assertIn("runuser", calls)
        self.assertFalse(self.stamp.exists())

    def test_kolla_failure_fails_without_stamp(self) -> None:
        result = self._run(runuser=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepare", self._calls())
        self.assertFalse(self.stamp.exists())

    def test_already_deployed_is_a_noop_success(self) -> None:
        self.stamp.write_text("AA:BB\n")
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._calls(), ["certbot", "openssl"])

    def test_certbot_failure_aborts_before_any_push(self) -> None:
        result = self._run(certbot=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._calls(), ["certbot"])


if __name__ == "__main__":
    unittest.main()

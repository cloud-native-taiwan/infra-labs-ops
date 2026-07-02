"""Render tests for roles/haproxy/templates/haproxy.cfg.j2.

The template uses only native Jinja2 filters (join, map, default, lower), so it
renders under a plain Jinja2 environment exactly as the render_templates.py
script exercises the network/grub templates.
"""

import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

ANSIBLE_DIR = Path(__file__).resolve().parents[1]
HAPROXY_TEMPLATES = ANSIBLE_DIR / "roles/haproxy/templates"


def _base_vars() -> dict:
    """Representative variables covering every conditional branch in the template."""
    return {
        "haproxy_stats_socket": "/run/haproxy/admin.sock",
        "haproxy_global_maxconn": 4096,
        "haproxy_ssl_bind_ciphers": ["ECDHE-ECDSA-AES128-GCM-SHA256", "ECDHE-RSA-AES128-GCM-SHA256"],
        "haproxy_ssl_bind_ciphersuites": ["TLS_AES_128_GCM_SHA256", "TLS_AES_256_GCM_SHA384"],
        "haproxy_ssl_bind_options": "ssl-min-ver TLSv1.2 no-tls-tickets",
        "haproxy_default_timeout_connect": "5s",
        "haproxy_default_timeout_client": "900s",
        "haproxy_default_timeout_server": "900s",
        "haproxy_default_timeout_http_request": "10s",
        "haproxy_default_timeout_http_keep_alive": "10s",
        "haproxy_default_timeout_queue": "30s",
        "haproxy_default_timeout_tunnel": "1h",
        "haproxy_bind_address": "*",
        "haproxy_http_port": 80,
        "haproxy_https_port": 443,
        "haproxy_cert_bundle": "/etc/haproxy/certs/cloudnative.tw.pem",
        "haproxy_http_redirect_to_https": True,
        "haproxy_unknown_host_status": 404,
        "haproxy_http_routes": [
            {"name": "harbor", "hostnames": ["Registry.CloudNative.tw"], "backend": "harbor"},
        ],
        "haproxy_backends": [
            {
                "name": "harbor",
                "health_check": {
                    "uri": "/api/v2.0/health",
                    "host": "registry.cloudnative.tw",
                    "expect_status": 200,
                },
                "servers": [
                    {
                        "name": "harbor",
                        "address": "127.0.0.1:8443",
                        "ssl": True,
                        "verify": "none",
                        "sni": "registry.cloudnative.tw",
                        "check": True,
                    },
                ],
            },
        ],
    }


def _render(overrides: dict | None = None) -> str:
    env = Environment(
        loader=FileSystemLoader(str(HAPROXY_TEMPLATES)),
        undefined=StrictUndefined,
    )
    variables = _base_vars()
    if overrides:
        variables.update(overrides)
    return env.get_template("haproxy.cfg.j2").render(**variables)


class HaproxyRenderTests(unittest.TestCase):
    def test_global_and_defaults_stanzas(self):
        rendered = _render()
        self.assertIn("global", rendered)
        self.assertIn("maxconn 4096", rendered)
        # Cipher lists are colon-joined.
        self.assertIn(
            "ssl-default-bind-ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256",
            rendered,
        )
        self.assertIn("ssl-default-bind-options ssl-min-ver TLSv1.2 no-tls-tickets", rendered)
        self.assertIn("timeout tunnel 1h", rendered)

    def test_https_frontend_bind_and_routing(self):
        rendered = _render()
        self.assertIn(
            "bind *:443 ssl crt /etc/haproxy/certs/cloudnative.tw.pem alpn h2,http/1.1",
            rendered,
        )
        # Route hostnames are lower-cased for the acl.
        self.assertIn("acl host_harbor hdr(host),lower -i registry.cloudnative.tw", rendered)
        self.assertIn("use_backend harbor if host_harbor", rendered)
        self.assertIn("default_backend deny_unknown", rendered)

    def test_http_frontend_redirects_when_enabled(self):
        rendered = _render()
        self.assertIn("frontend http_in", rendered)
        self.assertIn("redirect scheme https code 301", rendered)

    def test_http_frontend_denies_when_redirect_disabled(self):
        rendered = _render({"haproxy_http_redirect_to_https": False})
        self.assertNotIn("redirect scheme https code 301", rendered)
        # http_in falls through to the deny backend instead of redirecting.
        http_in = rendered.split("frontend https_in")[0]
        self.assertIn("default_backend deny_unknown", http_in)

    def test_deny_unknown_backend(self):
        rendered = _render()
        self.assertIn("backend deny_unknown", rendered)
        self.assertIn("http-request deny deny_status 404", rendered)

    def test_backend_health_check_and_server_options(self):
        rendered = _render()
        self.assertIn("backend harbor", rendered)
        self.assertIn("option httpchk", rendered)
        self.assertIn(
            "http-check send meth GET uri /api/v2.0/health hdr Host registry.cloudnative.tw",
            rendered,
        )
        self.assertIn("http-check expect status 200", rendered)
        self.assertIn(
            "server harbor 127.0.0.1:8443 ssl verify none sni str(registry.cloudnative.tw) check",
            rendered,
        )

    def test_backend_without_health_check_omits_httpchk(self):
        rendered = _render(
            {
                "haproxy_http_routes": [
                    {"name": "bmc", "hostnames": ["bmc.cloudnative.tw"], "backend": "bmc"},
                ],
                "haproxy_backends": [
                    {
                        "name": "bmc",
                        "servers": [{"name": "bmc", "address": "127.0.0.1:9080"}],
                    },
                ],
            }
        )
        self.assertNotIn("option httpchk", rendered)
        # A server with no ssl/sni still gets the default check flag.
        self.assertIn("server bmc 127.0.0.1:9080 check", rendered)


if __name__ == "__main__":
    unittest.main()

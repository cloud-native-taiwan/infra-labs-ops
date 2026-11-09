# Security-detection monitoring: design note

- **Status:** Partially implemented (cert-expiry + Watchdog + Keystone
  brute-force shipped; the items in "Deferred" are design-only)
- **Date:** 2026-07-03 (Keystone detection implemented 2026-07-04)
- **Deciders:** CNTUG ops

This note records what security-detection monitoring was added, the deploy-time
wiring the shipped rules depend on, and the assessment of the detections that
were intentionally not built as code yet (and why).

## Shipped in this change

| Detection | Where |
|---|---|
| Public TLS cert expiring soon | `kolla/config/prometheus/blackbox-alerts.rules` (`PublicEndpointCertExpiringSoon`) |
| Public TLS cert expiry imminent / expired | same file (`PublicEndpointCertExpiryImminent`) |
| Public endpoint unreachable | same file (`PublicEndpointProbeFailed`) |
| Alertmanager dead-man switch | `kolla/config/prometheus/watchdog-alerts.rules` (`Watchdog`) |
| Keystone failed-auth spike (added 2026-07-04) | `kolla/config/prometheus/control-plane-alerts.rules` (`KeystoneAuthFailureSpike`), gauge from `control-plane-alert-collector` |

Exact thresholds, windows and severities live in the rules files above, not in
this note.

All rules ship at `severity: warning`/`info` per the repo's warn-first soak
contract (`ansible/tests/test_prometheus_rules.py`); promotion to critical is a
one-label edit after soak.

### Deploy-time wiring these rules depend on

1. **Blackbox probe.** `globals.yml` now sets
   `enable_prometheus_blackbox_exporter: "yes"` and a non-empty
   `prometheus_blackbox_exporter_endpoints_custom`
   (`public_endpoint:http_2xx:https://openstack.cloudnative.tw`). Kolla renders
   the `blackbox_exporter` scrape job only when the endpoints list is non-empty,
   so this entry is what makes the probe (and therefore the cert/probe alerts)
   exist at all. Apply with:

   ```
   kolla-ansible -i <inventory> reconfigure -t prometheus
   ```

   Then confirm `probe_success` and `probe_ssl_earliest_cert_expiry` appear in
   live Prometheus (Status -> Targets, job `blackbox_exporter`). The blackbox
   exporter runs on the API interface; ensure the exporter host can resolve and
   reach every probed FQDN.

   Three public endpoints are probed, because they are distinct FQDNs:

   - `openstack.cloudnative.tw` (Horizon + all public APIs, one `haproxy.pem`
     cert, ADR-0019) via the `http_2xx` module -- the dashboard root returns
     200, so `probe_success` is a real end-to-end reachability signal.
   - `s3.cloudnative.tw:6780` (Ceph RGW S3/Swift) via the `tls_connect` module.
     RGW is a separate FQDN with its own cert; anonymous `GET /` returns 403
     (S3 auth), which would false-fail an `http_2xx` probe, so a raw TLS
     handshake is used to check the cert + port. The cert alerts fire per
     endpoint (they key on the `instance` label), so both certs are covered.
   - `console.cloudnative.tw` (Skyline, behind the edge HAProxy) via
     `http_2xx` on `/api/openstack/skyline/docs`: the console proxies that path
     to `skyline_apiserver`, whose `/docs` returns 200, so one probe covers
     both the console and the apiserver. Kolla also auto-adds Skyline probes on the VIP and
     `openstack.cloudnative.tw` ports 9998/9999 with the `os_endpoint` module,
     which requires a 200/300 body matching `versions`. Skyline never serves
     one (apiserver `/` is 404, console `/` is HTML), so those probes always
     fail and `PublicEndpointProbeFailed` excludes them by `service` label.

2. **Watchdog external heartbeat.** The `Watchdog` rule only fires an
   always-on signal; it is useless without an external monitor that alarms on
   the signal's *absence*. The Alertmanager config
   (`kolla/config/prometheus/prometheus-alertmanager.yml`) is vault-encrypted,
   so the route/receiver is documented here rather than inlined. Current state
   (2026-10-10): Watchdog is routed to a `"null"` receiver (dropped) so it no
   longer spams Telegram; no external heartbeat is wired yet. Add, under the
   existing `route.routes` and `receivers`:

   ```yaml
   route:
     routes:
       - matchers:
           - alertname = "Watchdog"
         receiver: deadmansswitch
         group_wait: 0s
         group_interval: 1m
         repeat_interval: 5m       # must be shorter than the external grace window
   receivers:
     - name: deadmansswitch
       webhook_configs:
         - url: "<heartbeat-url>"  # Dead Man's Snitch / healthchecks.io / Grafana OnCall
           send_resolved: false
   ```

   Point `<heartbeat-url>` at a monitor whose grace period (e.g. 15m) is wider
   than `repeat_interval`, so a genuine Prometheus/Alertmanager outage trips the
   external alarm within one window. Re-encrypt the file with
   `ansible-vault` before committing; never commit the heartbeat URL in
   plaintext.

## Keystone failed-auth visibility (implemented 2026-07-04)

**Goal.** The lockout policy in `kolla/config/keystone.conf` throttles brute force per
account but emits no signal an operator can see; a spray across many accounts
was invisible.

**Infra constraints found.** There is no centralized log store or CADF
notification consumer to query for auth failures, so the signal had to come
from the per-controller Keystone request log on the host.

**What was built (Option 1 below).** The `control-plane-alert-collector` role
now emits, on controllers only:

- `cpa_keystone_auth_failures_recent` -- count of failed password auths
  (`401` on `POST /v3/auth/tokens`) within a trailing window, parsed from the
  current Keystone uWSGI log (and the rotated log when the current file does
  not cover the window). It uses no byte offsets or monotonic counters, since
  offsets reset on rotation. See the collector role for parsing details and
  defaults.
- `cpa_keystone_auth_check_failed 1` fail-safe when the count cannot be
  trusted (feeds the existing `ControlPlaneCollectorCheckFailed` rule), so a
  broken parse never reads as a healthy zero.

The alert `KeystoneAuthFailureSpike`
(`kolla/config/prometheus/control-plane-alerts.rules`) ships at
`severity: warning` per the warn-first soak contract; its threshold and
window are defined in that file.

**Known limitation.** This is a volume-spike detector, not per-source
attribution. Attribution needs CADF events (Option 3) or haproxy-level
logging.

**Options considered.**

1. **Textfile-collector over the Keystone request log (chosen).** Extend the
   existing `control-plane-alert-collector` role (it already runs
   per-controller and writes node_exporter textfile gauges) with the gauge +
   fail-safe + alert described above. Reuses an established pattern, no new
   infrastructure. Built only after the log path/format was confirmed live on
   a controller, per this repo's "never fires silently" bar.

2. **Enable central logging + a log-based metric.** Turn on
   `enable_central_logging`, ship Keystone logs to OpenSearch, and drive
   detection from there (or via an exporter). Most general, but adds a logging
   pipeline and storage cost, and OpenSearch has no native Prometheus alerting
   path without extra glue.

3. **Keystone CADF notifications.** Emit `identity.authenticate` CADF events
   (`outcome=failure`) to the message bus and run a small consumer that counts
   them. Most structured and the least log-format-fragile, but requires a new
   consumer service (new infrastructure).

**Outcome.** Option 1 shipped after the one-time live confirmation of the
uWSGI log path/format; fall back to Option 3 if that log proves unreliable or
per-source attribution becomes a requirement.

## Deferred follow-ups

Further detections (CADF-based privilege-change events, object-store access
anomalies, host SSH brute force) were considered and deferred. Their scope is
tracked privately by the operators rather than enumerated in this public repo.

## References

- `kolla/config/prometheus/blackbox-alerts.rules`,
  `kolla/config/prometheus/watchdog-alerts.rules`
- ADR-0019 (auto TLS cert renewal), ADR-0023 (control-plane alerting)
- `ansible/roles/control-plane-alert-collector/` (textfile-collector pattern)

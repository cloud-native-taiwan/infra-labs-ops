# Security-detection monitoring: design note

- **Status:** Partially implemented (cert-expiry + Watchdog shipped; Keystone
  brute-force and the items in "Deferred" are design-only)
- **Date:** 2026-07-03
- **Deciders:** CNTUG ops

This note records what security-detection monitoring was added, the deploy-time
wiring the shipped rules depend on, and the assessment of the detections that
were intentionally not built as code yet (and why).

## Shipped in this change

| Detection | Where | Threshold |
|---|---|---|
| Public TLS cert expiring < 14d | `kolla/config/prometheus/blackbox-alerts.rules` (`PublicEndpointCertExpiringSoon`) | 14d..7d band, warning |
| Public TLS cert expiring < 7d / expired | same file (`PublicEndpointCertExpiryImminent`) | < 7d, warning (promotes to critical post-soak) |
| Public endpoint unreachable | same file (`PublicEndpointProbeFailed`) | `probe_success == 0` for 5m, warning |
| Alertmanager dead-man switch | `kolla/config/prometheus/watchdog-alerts.rules` (`Watchdog`) | always firing, info |

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
   reach both probed FQDNs.

   Two public endpoints are probed, because they are distinct FQDNs with
   distinct certificates:

   - `openstack.cloudnative.tw` (Horizon + all public APIs, one `haproxy.pem`
     cert, ADR-0019) via the `http_2xx` module -- the dashboard root returns
     200, so `probe_success` is a real end-to-end reachability signal.
   - `s3.cloudnative.tw:6780` (Ceph RGW S3/Swift) via the `tls_connect` module.
     RGW is a separate FQDN with its own cert; anonymous `GET /` returns 403
     (S3 auth), which would false-fail an `http_2xx` probe, so a raw TLS
     handshake is used to check the cert + port. The cert alerts fire per
     endpoint (they key on the `instance` label), so both certs are covered.

2. **Watchdog external heartbeat.** The `Watchdog` rule only fires an
   always-on signal; it is useless without an external monitor that alarms on
   the signal's *absence*. The Alertmanager config
   (`kolla/config/prometheus/prometheus-alertmanager.yml`) is vault-encrypted,
   so the route/receiver is documented here rather than inlined. Add, under the
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

## Keystone failed-auth visibility (assessed, not yet built)

**Goal.** The lockout policy in `kolla/config/keystone.conf`
(`lockout_failure_attempts = 5`, `lockout_duration = 1800`) throttles brute
force per account but emits no signal an operator can see; a spray across many
accounts is invisible today.

**Infra constraints found.**

- Central logging is **off** (`enable_central_logging` defaults to `no`;
  `enable_fluentd_systemd` is gated on it). OpenSearch runs, but only because
  `cloudkitty_storage_backend: "opensearch"` -- it indexes metering data, not
  service logs. So there is no centralized log store to query for auth failures.
- There is no CADF/notification consumer deployed.
- Keystone runs under Apache/WSGI; failed password auth is a `401` on
  `POST /v3/auth/tokens` in the per-controller Keystone Apache access log.

**Options.**

1. **Textfile-collector over the Keystone access log (recommended, smallest
   real detection).** Extend the existing `control-plane-alert-collector` role
   (it already runs per-controller and writes node_exporter textfile gauges) to
   emit a gauge such as `cpa_keystone_auth_failures_recent` = count of `401`s on
   the token endpoint within a trailing window, plus the same fail-safe
   (`*_check_failed 1`) the role uses when a check cannot run. Add an alert
   `KeystoneAuthFailureSpike` on that gauge. Reuses an established pattern, no
   new infrastructure.

   *Why this is a note and not code yet:* it cannot be written safely offline.
   The exact access-log path and format must be confirmed on a live controller
   first, and log rotation must be handled (parse a bounded tail / dedupe by
   timestamp, not a monotonic counter). Shipping an unverified log-scraper would
   violate this repo's explicit "never fires silently" bar (every `.rules`
   header already carries a metric-confirmation warning for exactly this
   reason). Once the log path/format is confirmed on `openstack01`, this is
   roughly one collector function + one alert rule + tests.

2. **Enable central logging + a log-based metric.** Turn on
   `enable_central_logging`, ship Keystone logs to OpenSearch, and drive
   detection from there (or via an exporter). Most general, but adds a logging
   pipeline and storage cost, and OpenSearch has no native Prometheus alerting
   path without extra glue.

3. **Keystone CADF notifications.** Emit `identity.authenticate` CADF events
   (`outcome=failure`) to the message bus and run a small consumer that counts
   them. Most structured and the least log-format-fragile, but requires a new
   consumer service (new infrastructure).

**Recommendation.** Option 1 after a one-time live confirmation of the Keystone
access-log path/format; fall back to Option 3 if that log proves unreliable.

## Deferred follow-ups

These were explicitly out of scope for this change; captured here so they are
not lost.

- **CADF admin-role-assignment events.** Alert when a `role_assignment` grants
  the `admin` role (privilege escalation / persistence signal). Needs the CADF
  notification pipeline from Option 3 above; best done together with Keystone
  brute-force detection since both ride the same events.
- **RGW anomalous access.** Detect unusual Ceph RGW (S3/Swift) access patterns
  -- spikes in `4xx`/`5xx`, large egress, or access from unexpected sources.
  Feasible from RGW/beast access logs or the RGW usage API; needs a collector or
  log pipeline and a baseline before thresholds can be set.
- **SSH brute force on the fleet hosts.** Detect repeated failed SSH auth on the
  OpenStack/Ceph hosts. A node-exporter textfile collector over
  `journalctl -u ssh`/`auth.log` failure counts, or fail2ban with a metrics
  exporter, would fit the existing per-host textfile pattern.

## References

- `kolla/config/prometheus/blackbox-alerts.rules`,
  `kolla/config/prometheus/watchdog-alerts.rules`
- ADR-0019 (auto TLS cert renewal), ADR-0023 (control-plane alerting)
- `ansible/roles/control-plane-alert-collector/` (textfile-collector pattern)

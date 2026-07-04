# Security-detection monitoring: design note

- **Status:** Partially implemented (cert-expiry + Watchdog + Keystone
  brute-force shipped; the items in "Deferred" are design-only)
- **Date:** 2026-07-03 (Keystone detection implemented 2026-07-04)
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
| Keystone failed-auth spike (added 2026-07-04) | `kolla/config/prometheus/control-plane-alerts.rules` (`KeystoneAuthFailureSpike`), gauge from `control-plane-alert-collector` | > 10 failures per node (~30 fleet-wide) / 10-min window for 5m, warning |

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

## Keystone failed-auth visibility (implemented 2026-07-04)

**Goal.** The lockout policy in `kolla/config/keystone.conf`
(`lockout_failure_attempts = 5`, `lockout_duration = 1800`) throttles brute
force per account but emits no signal an operator can see; a spray across many
accounts was invisible.

**Infra constraints found.**

- Central logging is **off** (`enable_central_logging` defaults to `no`;
  `enable_fluentd_systemd` is gated on it). OpenSearch runs, but only because
  `cloudkitty_storage_backend: "opensearch"` -- it indexes metering data, not
  service logs. So there is no centralized log store to query for auth failures.
- There is no CADF/notification consumer deployed.
- Keystone runs under **uWSGI** in this release (an earlier draft of this note
  assumed Apache/WSGI -- live confirmation on 2026-07-04 showed the Apache
  access logs are dead since Nov 2025). Failed password auth is a `401` on
  `POST /v3/auth/tokens` in the per-controller
  `/var/log/kolla/keystone/keystone-uwsgi.log` (`root:kolla 0640`; the
  collector runs as root and can read it).

**What was built (Option 1 below).** The `control-plane-alert-collector` role
now emits, on controllers only:

- `cpa_keystone_auth_failures_recent` -- count of `401` responses to
  `POST /v3/auth/tokens` whose uWSGI timestamp (`[Sat Jul  4 07:13:09 2026]`
  asctime, UTC) falls within a trailing window (default 600 s), parsed from a
  bounded tail (default 20000 lines) of the current log file -- no byte
  offsets or monotonic counters (offsets reset on rotation). A bounded tail
  alone is offset-safe but not window-safe: when the current file's tail does
  not cover the full window (e.g. right after logrotate), a bounded tail of
  `<log>.1` is prepended so an in-window burst that just rotated away still
  counts. Future timestamps (clock skew) never count. The status match is
  anchored on the parenthesized `(HTTP/1.x 401)` so byte counts like
  `401 bytes` cannot false-match, and `GET /v3/auth/tokens 401` (routine
  expired-token validation) is excluded.
- `cpa_keystone_auth_check_failed 1` fail-safe when the count cannot be
  trusted (feeds the existing `ControlPlaneCollectorCheckFailed` rule):
  missing/unreadable log, invalid window/tail overrides, a non-empty tail with
  zero recognizable uWSGI request lines (format drift would otherwise read as
  a trusted 0), or a saturated tail whose oldest line is still in-window (the
  count is then a floor, emitted alongside the fail-safe, not silently
  trusted).

The alert `KeystoneAuthFailureSpike`
(`kolla/config/prometheus/control-plane-alerts.rules`) fires at `> 10`
failures per node sustained for 5 m, `severity: warning` per the warn-first
soak contract. Threshold rationale: haproxy balances the API VIP roughly
evenly across the 3 controllers, so > 10 per node is ~30 fleet-wide;
`lockout_failure_attempts = 5` caps a single account at 5 failures per
lockout window, so a sustained ~30 means a spray across accounts, not one
user's typos.

**Known limitation.** The logged client IP is haproxy's internal address, not
the real client -- this is a volume-spike detector, not per-source
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

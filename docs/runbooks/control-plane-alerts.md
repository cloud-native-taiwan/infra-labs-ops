# Control-plane service alerts (soak, promotion, and health-gate mapping)

Operator companion to the control-plane alerting added for OpenStack
control-plane and infra services that were scraped but never alerted on
(Galera, RabbitMQ, OVN, Kolla container liveness). It covers what the alerts
are, how to deploy them, the warn-first soak and promotion to paging, and the
mapping back to the `health-gate` predicates they continuously enforce.

Files:

- `kolla/config/prometheus/control-plane-alerts.rules` -- the alert rules
  (auto-loaded from `node_custom_config`; no Prometheus config change).
- `ansible/roles/control-plane-alert-collector/` +
  `ansible/playbooks/setup-control-plane-alert-collector.yml` -- the
  per-host textfile collector for the two landmines metrics express poorly.
- `kolla/globals.yml` -- `prometheus_node_exporter_cmdline_extras` enables the
  node_exporter textfile collector that scrapes the collector's `.prom`.
- `ansible/tests/test_prometheus_rules.py`,
  `ansible/tests/test_control_plane_collector.py` -- the test harness.

## Two paths

- **PromQL path** -- conditions that map to metrics the exporters already
  scrape (Galera wsrep, RabbitMQ, cAdvisor). Pure rule files.
- **Textfile path** -- the RabbitMQ-partition and OVN-stale-chassis landmines.
  These cannot be faithfully expressed from current metrics: the
  `partition_handling=ignore` split-brain test needs every node's own view
  (a minority node lies), and the OVN check is a chassis set-difference plus a
  local `ovn-controller` liveness probe. `control_plane_alert_collector.py`
  runs the same `docker exec` checks `health-gate` uses and writes a `.prom`
  textfile that node_exporter re-exposes.

## Deploy order

1. **Land the rule files** (Phase A, standalone). `git pull` on the deploy host
   and run the Prometheus config deploy (`kolla-ansible -i ... reconfigure -t
   prometheus`). The Galera, RabbitMQ-metric, and container-liveness alerts
   start working immediately.
2. **Confirm metric names against live Prometheus** (do not skip -- a wrong
   name is a rule that never fires, silently). In the Prometheus UI, check each
   metric the rules use actually exists:
   - `mysql_global_status_wsrep_local_state`, `..._wsrep_ready`,
     `..._wsrep_cluster_size` (mysqld_exporter)
   - `rabbitmq_alarms_memory_used_watermark`, and the `up{job=~".*rabbitmq.*"}`
     target label (confirm Kolla's actual rabbitmq job name)
   - `container_start_time_seconds`, `container_last_seen` (cAdvisor)
   Adjust the rule file if a name or label differs, re-run the promtool tests,
   redeploy.
3. **Enable the textfile collector** (Phase B). The
   `prometheus_node_exporter_cmdline_extras` change in `kolla/globals.yml`
   needs a node_exporter redeploy (`... reconfigure -t prometheus`). Before
   building on it, verify the `/host` rootfs mount the flag assumes really
   exists on the running container:
   `docker inspect prometheus_node_exporter | grep -A2 '/host'`. If a future
   Kolla dropped it, this becomes a container override (see the note in
   `kolla/globals.yml`).
4. **Deploy the collector** (Phase B). `ansible-playbook
   playbooks/setup-control-plane-alert-collector.yml`. The role self-tests the
   script (`--check`) before enabling the timer. Characterize on one host
   first: run the unit by hand and inspect the output --
   `systemctl start control-plane-alert-collector.service` then
   `cat /var/lib/node_exporter/textfile_collector/control-plane-landmines.prom`
   -- before trusting the fleet.
5. **Verify the gauges scrape.** In Prometheus, query `cpa_collector_last_run_timestamp_seconds`;
   one series per chassis host (openstack01/02/04/05) confirms the loop is closed.

## Warn-first soak and promotion

Everything ships at `severity: warning`. Both `warning` and `critical` already
route through the existing (vault-encrypted) Alertmanager config, so promotion
to paging is **a label edit only** -- no Alertmanager change, no new receiver.

Soak: run at `warning` long enough to see the alert's normal behaviour
(suggested: at least one full operational week incl. a maintenance window).
Watch for flapping; widen the `for:` debounce or the threshold rather than
promoting a noisy alert.

Promote a landmine to `critical` once it has soaked without false positives:

1. Edit its `labels.severity` from `warning` to `critical` in
   `kolla/config/prometheus/control-plane-alerts.rules`.
2. The `test_warn_first_no_critical_yet` guard in
   `ansible/tests/test_prometheus_rules.py` intentionally fails when a
   `critical` lands -- update that test to allow the now-promoted alert (it is
   the checklist's tripwire, not an obstacle), keeping it asserting the set of
   alerts that are *deliberately* critical.
3. Run the promtool tests, redeploy Prometheus config.

Promotion candidates (the true landmines): `GaleraNodeNotSynced`,
`GaleraClusterShrunk`, `RabbitMQPartitionDetected`, `RabbitMQMembershipShrunk`,
`OVNChassisMissing`, `OVNChassisStale`, `OVNControllerNotConnected`,
`KollaContainerDown`.

## Alert -> health-gate predicate mapping (R10)

When a `health-gate` predicate changes, the mirrored alert must follow. This
table is the manual link (v1 mirrors rather than sharing a library).

| Alert | health-gate predicate |
|---|---|
| `GaleraNodeNotSynced` | `tasks/galera.yml` -- state == `Synced` |
| `GaleraNodeNotReady` | `tasks/galera.yml` -- ready == `ON` / status == `Primary` |
| `GaleraClusterShrunk` | `tasks/galera.yml` -- size == expected (3) |
| `RabbitMQPartitionDetected` | `tasks/rabbitmq.yml` -- partitions == 0 (every node) |
| `RabbitMQMembershipShrunk` | `tasks/rabbitmq.yml` -- running_nodes == expected (every node) |
| `OVNChassisMissing` | `tasks/ovn.yml` -- no expected chassis missing |
| `OVNChassisStale` | `tasks/ovn.yml` -- no stale chassis rows |
| `OVNControllerNotConnected` | `tasks/ovn.yml` -- ovn-controller SB status == `connected` |
| `RabbitMQMemoryAlarm` | none -- RabbitMQ-native memory alarm (not a gate check) |
| `RabbitMQNodeDown` | none -- scrape liveness; complements the membership check |
| `KollaContainerRestartLooping` | none -- cAdvisor crash-loop detection |
| `KollaContainerDown` | none -- cAdvisor stopped-container detection |
| `ControlPlaneCollectorCheckFailed` | mirrors health-gate's refuse-rather-than-guess: a check that could not run does not read as healthy |
| `ControlPlaneCollectorStale` | none -- collector self-health (textfile not refreshed) |

## Security model

The collector runs as **root** on every chassis host (it `docker exec`s into
the privileged Kolla `rabbitmq` / `ovn_sb_db` / `ovn_controller` containers, the
same access `health-gate` uses). The systemd unit sets `NoNewPrivileges=true`
and `PrivateTmp=true`; the script reads no secrets and writes only the
world-readable `.prom` textfile (atomically, via `mkstemp` in the output dir).
Because this repo is public, keep it that way: the collector must never read
Kolla passwords, clouds.yaml, or vault material -- the checks it mirrors are all
unauthenticated CLI status queries.

## Known gaps

- **Docker `unhealthy` but running.** The motivating incident was an
  `opensearch` container stuck Docker-`unhealthy` for four weeks while still
  running. cAdvisor does not expose Docker healthcheck status, so
  `KollaContainerDown` (last-seen staleness) only catches a container that
  actually **stopped**, and `KollaContainerRestartLooping` only catches one
  that restarts. A container that stays up but unhealthy is **not** caught
  today. Follow-up: emit a `docker inspect`/`docker ps --filter health=unhealthy`
  gauge from `control_plane_alert_collector.py` (it already does `docker exec`
  per host) and add a rule over it.
- **`KollaContainerDown` catches the stop transition, not steady-state
  absence.** cAdvisor stops exporting a removed container's series, and once it
  is older than Prometheus `--query.lookback-delta` (default 5m) the
  `time() - container_last_seen` subtraction has no data, so the alert fires for
  a few minutes around the stop and then auto-resolves. A container that has
  been gone for hours produces no `container_last_seen` series at all and is not
  alerted. The threshold (120s) is deliberately below lookback-delta so the
  transition is caught; keep it there if lookback-delta is changed. The
  `docker ps` follow-up above is the durable fix for steady-state absence too.
  Short-lived cephadm helper containers that use the Ceph image with random
  Docker names are intentionally excluded; real cephadm daemon containers remain
  covered through their `ceph-*` names.
- **Collector never ran at all.** `ControlPlaneCollectorStale` needs the
  `cpa_collector_last_run_timestamp_seconds` series to exist; if the collector
  never produced a file on a host the series is absent and the alert is silent.
  Step 5 of the deploy (one series per chassis host) is the manual check that
  closes this; an `absent()`-based rule is a possible hardening.
- **Cluster-size thresholds are hardcoded** (Galera/RabbitMQ expect 3, OVN
  expects 4 chassis). PromQL cannot read the Ansible inventory. If the
  `controller`/`compute` group membership changes, update the rule file
  thresholds and the collector defaults together.

# control-plane-alert-collector

Deploys a per-host systemd timer that runs `control_plane_alert_collector.py`
periodically, writing the two control-plane landmines that metrics express
poorly as Prometheus textfile gauges for node_exporter's `--collector.textfile`:

- **RabbitMQ partition / membership.** This fleet deliberately runs
  `partition_handling=ignore` (Bug #2126768); a minority-side node does not
  self-pause, so one node's `cluster_status` can lie. Every controller runs the
  collector and emits its OWN view of `running_nodes` / `partitions`; PromQL
  aggregates across nodes (U4).
- **OVN missing/stale chassis + ovn-controller liveness.** Compares the SB-DB
  chassis set against the expected set (missing/stale) and probes each chassis
  host's LOCAL ovn-controller SB connection status via `ovn-appctl` (a stale
  Chassis row outlives a dead ovn-controller, so the probe must run locally --
  hence this role targets the `compute` group, not only controllers).

The logic deliberately mirrors `roles/health-gate/tasks/{rabbitmq,ovn}.yml`;
when either side changes, sync via the mapping table in
`docs/runbooks/control-plane-alerts.md` (R10).

**Fail-safe:** any CLI error or unexpected output emits only `*_check_failed 1`
and never a healthy value; the whole file is written atomically (temp+rename).
If the collector dies the file stops refreshing, caught by U4's
collector-staleness alert.

Deploy: `ansible-playbook playbooks/setup-control-plane-alert-collector.yml`
(first enable the textfile collector in `kolla/globals.yml` and reconfigure
Kolla). Offline self-test: `python3 files/control_plane_alert_collector.py --check`.

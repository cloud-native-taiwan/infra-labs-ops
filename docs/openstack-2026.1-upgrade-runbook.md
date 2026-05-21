---
title: "ops: OpenStack 2025.2 → 2026.1 (Kolla-Ansible) upgrade runbook"
type: ops
status: scheduled
date: 2026-05-21
maintenance_window: 2026-05-24 21:00–24:00 UTC+8
origin: commits a40380d..c7c65f8 (2026.1 upgrade prep)
---

# ops: OpenStack 2025.2 → 2026.1 (Kolla-Ansible) upgrade runbook

## Summary

Upgrade the CNTUG Infra Labs control plane from OpenStack **2025.2** to **2026.1** using Kolla-Ansible. Inventory, `globals.yml`, and monitoring exporters were already prepared in commits `a40380d..c7c65f8`. This runbook is the deploy-night execution plan: pre-flight checks, image staging, the actual `kolla-ansible upgrade`, validation, manual `openstack06` cleanup, and rollback. Tenant compute instances stay running throughout; only the OpenStack control-plane APIs (Horizon, Skyline, Keystone, Nova/Neutron/Cinder/Glance/Heat/Octavia APIs) are interrupted.

---

## Maintenance Window

- **Start:** 2026-05-24 (Sunday) **21:00 UTC+8**
- **Planned end:** 2026-05-24 **24:00 UTC+8** (3-hour budget; expected ~150 min)
- **User-facing impact:** Horizon, Skyline, OpenStack APIs unavailable for the duration. Running compute instances and tenant networks continue to operate (dataplane is unaffected). All Horizon sessions are invalidated once when Valkey replaces the previous session backend; users must re-login.

---

## What's Changing

From `git log a40380d..c7c65f8`:

| Commit | Change | User impact |
| --- | --- | --- |
| `a40380d` | Inventory: rename `kolla-toolbox`→`kolla_toolbox`, `kolla-logs`→`kolla_logs`; drop Zun, Kuryr, InfluxDB, Telegraf stubs | None (services never deployed) |
| `60ed650` | `openstack_release` 2025.2 → 2026.1; enable `prometheus_openstack_network_exporter` (OVN/OVS) + `prometheus_valkey_exporter` | New metrics in Grafana; MariaDB needs disk headroom for 2 GB `innodb_log_file_size` default |
| `534b27b` | Pin `rabbitmq_allowed_upgrades` to upstream 2026.1 default (RabbitMQ 4.1 → 4.2) | Brief AMQP unavailability during cluster restart |
| `a498915` | Reverted Nova vTPM (Barbican prerequisite missing) | None — deferred to a future release |
| `d82866d` | Remove `non-openstack` from `common:children` (openstack06 stops receiving `cron`/`fluentd`/`kolla_logs`/`kolla_toolbox`) | Requires **manual container cleanup on openstack06** post-deploy (see §6) |
| `c7c65f8` | Add `[prometheus-openstack-network-exporter:children]` and `[prometheus-valkey-exporter:children]` inventory groups | New exporter containers on compute/network/control |

**Notable upstream 2026.1 behaviour that affects this deploy:**

- RabbitMQ minor upgrade 4.1 → 4.2 (single-minor jump, allowed implicitly).
- MariaDB `innodb_log_file_size` default raised to 2 GB → controllers need ≥ 4 GB free in the MariaDB volume.
- Horizon session backend migrates to Valkey → all sessions invalidated once.
- `glance-tls-proxy` is torn down during reconfigure (Glance internal TLS now handled in-process).
- Kolla 2026.1 enables all stable feature flags automatically; this includes the Khepri metadata store migration for RabbitMQ — Kolla handles the migration during `upgrade`.

---

## Pre-Flight (T-24h to T-1h)

Run from the deploy host (`localhost` in the `deployment` group of `kolla/multinode`). All `kolla-ansible` commands assume the standard venv at `/opt/kolla-ansible/`.

### 1. Verify the working tree matches what was reviewed

```bash
cd /Users/igene/Documents/cntug/infra-labs/infra-labs-ops    # or deploy-host path
git fetch origin
git log --oneline a40380d^..HEAD
# Expected tip: c7c65f8 fix(kolla): add inventory groups for new 2026.1 Prometheus exporters
git status                                                   # must be clean
```

### 2. Refresh the kolla-ansible venv to 2026.1

```bash
source /opt/kolla-ansible/bin/activate
pip install --upgrade 'kolla-ansible[ansible]==2026.1.*'
kolla-ansible install-deps
kolla-ansible --version          # should report 2026.1.x
```

### 3. Render and validate the 2026.1 configuration

```bash
cd kolla/
kolla-ansible -i multinode validate-config
kolla-ansible -i multinode prechecks
```

Expect both to pass cleanly. The most common failures on a 2025.2 → 2026.1 jump are (a) RabbitMQ version-jump policy and (b) MariaDB free space; both are addressed below.

### 4. MariaDB headroom check (all controllers: openstack01, openstack02, openstack04)

```bash
ansible -i multinode control -m shell -a \
  'df -h /var/lib/docker/volumes/mariadb; du -sh /var/lib/docker/volumes/mariadb 2>/dev/null'
```

Confirm **≥ 4 GB free** on each. The 2 GB `innodb_log_file_size` default rewrites the redo log on first start; without headroom MariaDB will crash-loop.

### 5. RabbitMQ cluster health

```bash
ansible -i multinode control -m shell -a \
  'docker exec rabbitmq rabbitmqctl cluster_status | head -30'
```

All three nodes must be running and partition-free before starting the upgrade. If anything looks degraded, fix first — Kolla refuses to upgrade an unhealthy RabbitMQ cluster.

### 6. Ceph health (Ceph hosts cluster state, not part of upgrade but must be healthy)

```bash
ssh debian@openstack01 'sudo cephadm shell -- ceph -s'
```

Expect `HEALTH_OK` (or only known-acceptable warnings). Do **not** start the upgrade against a degraded Ceph.

### 7. Pre-pull 2026.1 images to every host

This is the longest pre-flight step (~20–40 min depending on registry bandwidth). Do it **before** the maintenance window starts so the actual cutover is fast.

```bash
kolla-ansible -i multinode pull
```

### 8. Snapshot MariaDB

```bash
kolla-ansible -i multinode mariadb_backup
# Backups land in /var/lib/docker/volumes/mariadb_backup/_data/
ansible -i multinode control -m shell -a \
  'ls -lh /var/lib/docker/volumes/mariadb_backup/_data/ | tail -5'
```

Confirm the backup file is fresh and non-empty on **all three** controllers.

### 9. Snapshot critical Kolla state

On every host (control + compute + monitoring):

```bash
ansible -i multinode 'control:compute:monitoring' -m shell -a \
  'sudo tar czf /root/kolla-config-pre-2026.1.tgz /etc/kolla /etc/systemd/system/kolla-* 2>/dev/null; ls -lh /root/kolla-config-pre-2026.1.tgz'
```

### 10. Telegram broadcast (T-1h)

Post the maintenance start reminder to `t.me/cntug_infra_labs`. Pin if not already pinned from the T-3day notice.

---

## Cutover (T+0)

### Phase 1 — Freeze the control plane (21:00)

Stop accepting new operations cleanly; tenant instances are unaffected.

```bash
# Optional: disable HAProxy frontends for API/Horizon/Skyline so users hit a clear 503
# instead of a half-upgraded service. Skip if you'd rather rely on the upgrade itself.
ansible -i multinode control -m shell -a \
  'docker exec haproxy bash -c "echo disable frontend openstack_api | socat stdio /var/lib/kolla/haproxy/haproxy.sock"' || true
```

### Phase 2 — Run the upgrade (21:05)

```bash
cd kolla/
time kolla-ansible -i multinode upgrade 2>&1 | tee \
  ../reports/logs/2026-05-24-kolla-upgrade-2026.1.log
```

Kolla runs the upgrade per-service in dependency order (MariaDB → RabbitMQ → Keystone → Glance → Placement → Nova → Neutron → Cinder → Heat → Octavia → Designate → Skyline → Horizon → monitoring stack). Expected wall time: **up to ~150 minutes** on this fleet.

**Watch for, do not panic on:**

- `glance-tls-proxy` container being **removed** during the Glance step (expected — TLS is in-process in 2026.1).
- RabbitMQ rolling restart taking 2–3 minutes per node while Khepri migrates.
- A one-time `mariadb-upgrade` step that touches every schema (`mariadb_recovery` is NOT what we want here; Kolla calls `mariadb_upgrade` automatically).

**If the upgrade aborts mid-run:** capture the failing task name and host, then jump to §Rollback before retrying. Do not re-run `upgrade` against a partially-upgraded fleet without understanding why it stopped.

### Phase 3 — Re-enable HAProxy frontends (if disabled in Phase 1)

```bash
ansible -i multinode control -m shell -a \
  'docker exec haproxy bash -c "echo enable frontend openstack_api | socat stdio /var/lib/kolla/haproxy/haproxy.sock"' || true
```

---

## Post-Deploy Validation (T+90min)

### 1. Container fleet sanity

```bash
ansible -i multinode 'control:compute' -m shell -a \
  'docker ps --filter "status=running" --format "{{.Names}}\t{{.Image}}" | grep 2026.1 | wc -l'

ansible -i multinode 'control:compute' -m shell -a \
  'docker ps --filter "status=exited" --filter "status=restarting" --format "{{.Names}}\t{{.Status}}"'
```

Expect zero entries in the second list (or only known-stopped containers like `glance-tls-proxy` on controllers).

### 2. OpenStack API smoke tests

From the deploy host with admin RC sourced:

```bash
source /etc/kolla/admin-openrc.sh
openstack endpoint list -f value -c 'Service Name' -c URL | sort -u
openstack compute service list
openstack network agent list
openstack volume service list
openstack hypervisor list
openstack image list --limit 5
openstack server list --all-projects --limit 5
```

Every service should show `up` / `enabled`. Hypervisors must equal the count of compute hosts (openstack01, openstack02, openstack04, openstack05).

### 3. Tenant golden-path test

```bash
openstack server create --image cirros --flavor m1.tiny \
  --network <known-tenant-net> --wait upgrade-smoke-$(date +%H%M)
openstack server show upgrade-smoke-*
openstack server delete upgrade-smoke-*
```

### 4. Skyline external Swift access

Skyline talks to Swift over the external endpoint. Verify the trusted CA bundle in the 2026.1 image still trusts the public cert:

```bash
ansible -i multinode control[0] -m shell -a \
  'docker exec skyline_apiserver curl -sS -o /dev/null -w "%{http_code}\n" \
     https://openstack.cloudnative.tw:8080/v1/AUTH_admin/'
```

Expect `401` (auth required) — **not** `60` (cert verification failed). A 60 means the container's CA bundle lost trust for the external cert and Skyline's object-storage browser will be broken.

### 5. New Prometheus exporters

```bash
ansible -i multinode 'compute:network' -m shell -a \
  'docker ps --format "{{.Names}}" | grep prometheus_openstack_network_exporter'

ansible -i multinode control -m shell -a \
  'docker ps --format "{{.Names}}" | grep prometheus_valkey_exporter'
```

Confirm targets are scraping in Prometheus (`https://stats.cloudnative.tw/`):
- `prometheus_openstack_network_exporter`: one target per compute + network host.
- `prometheus_valkey_exporter`: one target per control host (where Valkey runs).

### 6. Horizon / Skyline login

Manually log into Horizon and Skyline as `admin` and one tenant user. Confirm:
- Login succeeds (sessions are fresh — Valkey backend now).
- Project / instance / network views render.
- Cinder volume list renders.

---

## Manual openstack06 Cleanup

`d82866d` removed `non-openstack` from `common:children`, but **the running containers on openstack06 are not removed by `kolla-ansible upgrade`**. Clean them up manually after the deploy is validated:

```bash
ssh debian@openstack06
sudo docker ps --format '{{.Names}}' | grep -E '^(cron|fluentd|kolla_logs|kolla_toolbox)$'

# For each container that the previous command lists:
for c in cron fluentd kolla_logs kolla_toolbox; do
  sudo docker stop "$c" 2>/dev/null || true
  sudo docker rm   "$c" 2>/dev/null || true
done

# Remove any associated systemd units Kolla may have installed:
sudo find /etc/systemd/system -name 'kolla-*' \( -name '*cron*' -o -name '*fluentd*' -o -name '*kolla_logs*' -o -name '*kolla_toolbox*' \) -print
# Inspect, then disable + remove the matching units, then `systemctl daemon-reload`.
```

Record the removed container list in the deploy log under `reports/logs/2026-05-24-kolla-upgrade-2026.1.log`.

---

## Rollback

**Rollback is destructive to state created during the upgrade window.** Trigger only if:
- The upgrade aborts and re-running fails to converge, **or**
- A core API service is unhealthy and validation in §Post-Deploy Validation fails irrecoverably.

### Fast path (one or two services regressed)

If only one or two services regressed (e.g., Octavia), pin the previous image tag for that service in `kolla/globals.yml` and re-run `kolla-ansible -i multinode deploy --tags <service>`. Do **not** roll back the whole stack for a single-service regression.

### Full rollback

```bash
# 1. Stop the control plane
ansible -i multinode 'control:compute' -m shell -a \
  'docker ps --format "{{.Names}}" | xargs -r docker stop'

# 2. Revert the config + inventory to the pre-upgrade tip
cd /Users/igene/Documents/cntug/infra-labs/infra-labs-ops
git switch -c rollback/2025.2 c7c65f8^   # one commit before the upgrade series
# (or `git revert a40380d..c7c65f8` if you prefer to keep history linear)

# 3. Restore MariaDB from the §Pre-Flight 8 backup
kolla-ansible -i kolla/multinode mariadb_recovery

# 4. Re-deploy 2025.2
kolla-ansible -i kolla/multinode pull
kolla-ansible -i kolla/multinode deploy
```

Expect a full rollback to take 60–120 minutes. Communicate immediately to users via Telegram if rollback is triggered — extends the outage past the announced window.

---

## Communications

- **T-3 days (2026-05-21):** Publish [`docs/announcements/2026-05-21-openstack-2026.1-upgrade-maintenance.md`](../announcements/2026-05-21-openstack-2026.1-upgrade-maintenance.md) via email + Telegram channel.
- **T-1 hour (2026-05-24 20:00):** Post Telegram reminder.
- **T+0:** Update Telegram with “maintenance started”.
- **T+done:** Post completion notice with summary (what changed, anything users should know, when to re-login).

---

## Post-Upgrade Follow-Ups

Tracked separately, not blocking the upgrade:

- vTPM / Windows 11 support — requires Barbican key manager rollout before re-enabling `enable_nova_swtpm`. See commit `a498915`.
- New Prometheus exporter dashboards — add `prometheus_openstack_network_exporter` and `prometheus_valkey_exporter` panels to the existing Grafana boards.
- Update `docs/quickstart.md`, `docs/fleet-topology.md`, and `docs/troubleshooting.md` if any tenant-visible behavior changed (Horizon session timeout, Skyline URL, etc.).

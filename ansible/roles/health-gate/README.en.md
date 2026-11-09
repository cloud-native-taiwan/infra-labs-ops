# Health Gate Role

[中文](README.md)

`health-gate` is the HA pre-flight gate for disruptive operations (rolling
package upgrades, certificate renewal, and `playbooks/reboot.yml`).
Previously `upgrade.yml`'s only inter-host safety was a blind `pause: 30`;
this role gates each host with real checks that fail **closed**: if it
cannot prove the fleet is safe to disrupt, it aborts the play with a reason.
(The pause survives in `upgrade.yml` only as a post-apt container-settle
wait, not as a safety mechanism.) The role is read-only and never mutates
the fleet.

A 30-second sleep is invisible to every known fleet landmine — a Galera node
stuck in Donor state, a RabbitMQ split that one node's `cluster_status`
cannot see, OSDs that are not `ok-to-stop`, or a stale OVN
Chassis. This role checks each one before the disruption proceeds.

## Usage

Include it immediately before disrupting a host, with `serial: 1` so one host
is gated and disrupted at a time:

```yaml
- name: Run HA health gate before disrupting this host
  ansible.builtin.include_role:
    name: health-gate
  vars:
    health_gate_target_host: "{{ inventory_hostname }}"
```

`upgrade.yml` already wires this in. To run the upgrade unattended against a
host that carries a blocking hazard (see below), you must acknowledge it:

```bash
cd ansible
ansible-playbook playbooks/upgrade.yml -e health_gate_ack_hazards=true
```

## Checks

All checks are on by default and individually skippable
(`health_gate_<name>_enabled: false`). Skipping is an explicit, logged choice.

| Check | What "healthy" means | Why a blind sleep misses it |
| --- | --- | --- |
| **Galera** | Every controller node reports `wsrep_local_state_comment = Synced`, `wsrep_cluster_status = Primary`, `wsrep_ready = ON`, and `wsrep_cluster_size` equals the expected member count. | A node in Donor/Desync/Joining still answers TCP. Losing another node while one is Donor can break write quorum. |
| **RabbitMQ** | Queried from **every** node: no partitions, and `running_nodes` equals the expected size. | Each node's `cluster_status` lists only the peers it can reach, so a single node's view can look clean while the cluster is split. On RabbitMQ 4.3 (kolla 2026.2) `partitions` is always empty and `cluster_partition_handling` has no effect, so the `running_nodes` count is what catches a split ([ADR-0006](../../../docs/adr/0006-rabbitmq-messaging-topology.md)). |
| **Ceph** | `ceph health` is not `HEALTH_ERR` (and not `HEALTH_WARN` unless `health_gate_ceph_allow_warn=true`), no check in `health_gate_ceph_blocking_checks` (`MON_DOWN`, `MON_NETSPLIT`, `OSD_DOWN`, `OSD_HOST_DOWN`, `OSD_ROOT_DOWN`, `PG_AVAILABILITY`, `PG_DEGRADED`) is raised even with `allow_warn`, and both `ceph osd ok-to-stop <target host OSD ids>` and `ceph mon ok-to-stop <target host mons>` succeed. | A cluster can be `HEALTH_WARN` for benign reasons yet still unsafe to lose a specific OSD or monitor. `ok-to-stop` is the authoritative answer; the health string alone is not, and a blanket `allow_warn` would otherwise also accept a down monitor. |
| **OVN** | Every host expected to run ovn-controller (the `compute` group) has a Chassis row in the OVN SB DB, there are no unexpected (stale) rows, **and** each host's ovn-controller daemon reports SB connection status `connected` (`ovn-appctl -t ovn-controller connection-status`). | A stale Chassis makes the logical topology lie about where ports/gateways live — and a row alone is not liveness: a dead ovn-controller leaves its row behind, so the set comparison alone would still pass. |
| **OVN raft** (`health_gate_ovn_raft_enabled`, runs when OVN is enabled) | On every controller, `ovn-appctl -t /run/ovn/ovn{nb,sb}_db.ctl cluster/status` reports `Status: cluster member`, role leader or follower, a known leader, and as many servers as controllers. Polled about 2 min. | Chassis rows can look fine while one NB/SB raft member has dropped out or an election is stuck; disrupting another controller then loses quorum. |
| **Hazards** | The target host has no unacknowledged blocking hazard (`blocks_unattended: true`). | See [Hazards](#per-host-hazards). |

Every remote command has a per-attempt `timeout`
(`health_gate_command_timeout_seconds`, default 300 s), so a hung
`docker exec` or `cephadm shell` fails the gate instead of hanging the play.
The ovn-controller probe polls until `connected` (about 2 min) before the
assert fails, so a just-rebooted chassis gets time to reconnect.

### Where the checks run

- Galera and RabbitMQ run as Kolla Docker containers on the controllers; the
  role `delegate_to`s each controller and runs `docker exec`.
- The Ceph CLI exists only inside a `cephadm shell` on the bootstrap host
  (`groups['ceph_bootstrap'][0]`); all Ceph commands delegate there.
- OVN SB is read via `ovn-sbctl` inside the `ovn_sb_db` container on the first
  controller; the liveness probe delegates to each compute and runs
  `ovn-appctl` inside its `ovn_controller` container. The raft check
  delegates to every controller and runs `ovn-appctl cluster/status` inside
  `ovn_nb_db` and `ovn_sb_db`.

## Per-host hazards

`host_vars/<host>.yml` carries a machine-readable `host_hazards` list — the
fleet's landmine map, consulted as data instead of relying on operator memory.
Each entry has:

- `key`: stable identifier (e.g. `bond0_boot_failure`).
- `reason`: human explanation of the hazard.
- `source`: doc reference (e.g. `docs/troubleshooting.md#...`).
- `blocks_unattended` (optional): `true` means the gate refuses an unattended
  disruption of this host unless `health_gate_ack_hazards=true`.
- `canary_tier` (optional): numeric ordering for rolling operations — lower is
  disrupted earlier. `playbooks/reboot.yml` consumes this by walking
  `managed_hosts` in inventory order, and the structure tests assert that the
  inventory order matches these tiers. openstack06 (pure Ceph) is first;
  openstack01 (cephadm bootstrap + mgr + bond0 hazard) is last.

Current blocking hazard: **openstack01 `bond0_boot_failure`** — bond0 does not
come up automatically after reboot and needs a manual `systemctl restart
networking`, so it must never be rebooted unattended.

## Overriding (and why it is dangerous)

| Override | Effect | Danger |
| --- | --- | --- |
| `health_gate_<name>_enabled=false` | Skips that check entirely. | You disrupt the fleet without knowing that subsystem is safe. Only skip a check whose subsystem is genuinely out of scope (e.g. OVN on a Ceph-only maintenance with no network disruption). |
| `health_gate_ceph_allow_warn=true` | Accepts `HEALTH_WARN` and proceeds (refused by default), except when a blocking check (`health_gate_ceph_blocking_checks`) is raised. | Only set it after reading the warning and confirming it is benign (e.g. a lone noout flag). `ok-to-stop` still runs, but the health string may hide problems it cannot see. |
| `health_gate_ack_hazards=true` | Proceeds past a blocking hazard. | You are taking responsibility for recovering, e.g. bringing bond0 back up by hand on openstack01. Never set this in an unattended/cron context. |
| `health_gate_galera_container` / `health_gate_rabbitmq_container` | Override container names. | Wrong names make `docker exec` fail; the gate then fails closed (safe). |

Overriding never makes the fleet safer — it only suppresses the warning. The
gate exists because a blind sleep already proved insufficient.

## Waiting for Galera to converge

By default the Galera check fails on the first unsafe reading. Set
`health_gate_galera_wait_for_sync: true` to poll each node until it reports
Synced/Primary/ON at full size (`health_gate_galera_sync_retries`, default
60 x `health_gate_delay_seconds` per node; nodes are polled one after
another). `reboot.yml` enables it for the
post-reboot re-check only, because peers report the old cluster size for a
few seconds after a rebooted node rejoins.

## Waiting for Ceph to converge

`health_gate_ceph_wait_for_health: true` makes the Ceph check poll until
health is acceptable and `ok-to-stop` passes (`health_gate_ceph_wait_retries`,
default 60 x `health_gate_delay_seconds`) instead of failing on the first
reading. The network role enables it, together with the Galera wait, after a
bond0 restart as the reconvergence wait before the next host; the
pre-disruption gate leaves it off.

## Credentials

The role reads no secrets from the repo. The Galera query authenticates as
root with `health_gate_galera_password`, which defaults to Kolla's
`database_password` — supply it at runtime from the vaulted passwords file:

```bash
ansible-playbook ... -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

The password is fed to the client on stdin as a defaults file (never in
argv, so it is invisible to `ps`) and the task is `no_log`. Note the
MariaDB 11+ Kolla images ship no `mysql` symlink; the client is `mariadb`.

## File structure

```
ansible/roles/health-gate/
  defaults/main.yml   # toggles, container names, retries, hazard knobs
  tasks/
    main.yml          # orchestrator (hazards, galera, rabbitmq, ceph, ovn)
    hazards.yml       # consult host_hazards; refuse blocking hazards
    galera.yml        # wsrep Synced + cluster size, every node
    rabbitmq.yml      # no partitions + full membership, every node
    ceph.yml          # health acceptable + osd/mon ok-to-stop for target host
    ovn.yml           # chassis liveness, no stale chassis
    ovn_raft.yml      # NB/SB raft cluster/status on every controller
```

# Health Gate Role

[中文](README.md)

`health-gate` is the HA pre-flight gate for disruptive operations (rolling
package upgrades, certificate renewal, the future rolling-reboot play).
Previously `upgrade.yml`'s only inter-host safety was a blind `pause: 30`;
this role gates each host with real checks that fail **closed**: if it
cannot prove the fleet is safe to disrupt, it aborts the play with a reason.
(The pause survives in `upgrade.yml` only as a post-apt container-settle
wait, not as a safety mechanism.) The role is read-only and never mutates
the fleet.

A 30-second sleep is invisible to every known fleet landmine — a Galera node
stuck in Donor state, a RabbitMQ split-brain masked by
`partition_handling=ignore`, OSDs that are not `ok-to-stop`, or a stale OVN
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
| **RabbitMQ** | Queried from **every** node: no partitions, and `running_nodes` equals the expected size. | This fleet runs `partition_handling=ignore` on purpose (Bug #2126768, `stream_fanout=false`). `ignore` means a partitioned node does not self-pause, so a single node's `cluster_status` can look clean while the cluster is split. |
| **Ceph** | `ceph health` is not `HEALTH_ERR` (and not `HEALTH_WARN` unless `health_gate_ceph_allow_warn=true`), and `ceph osd ok-to-stop <target host OSD ids>` returns success. | A cluster can be `HEALTH_WARN` for benign reasons yet still unsafe to lose a specific OSD. `ok-to-stop` is the authoritative answer; the health string alone is not. |
| **OVN** | Every host expected to run ovn-controller (the `compute` group) has a Chassis row in the OVN SB DB, there are no unexpected (stale) rows, **and** each host's ovn-controller daemon reports SB connection status `connected` (`ovn-appctl -t ovn-controller connection-status`). | A stale Chassis makes the logical topology lie about where ports/gateways live — and a row alone is not liveness: a dead ovn-controller leaves its row behind, so the set comparison alone would still pass. |
| **Hazards** | The target host has no unacknowledged blocking hazard (`blocks_unattended: true`). | See [Hazards](#per-host-hazards). |

### Where the checks run

- Galera and RabbitMQ run as Kolla Docker containers on the controllers; the
  role `delegate_to`s each controller and runs `docker exec`.
- The Ceph CLI exists only inside a `cephadm shell` on the bootstrap host
  (`groups['ceph_bootstrap'][0]`); all Ceph commands delegate there.
- OVN SB is read via `ovn-sbctl` inside the `ovn_sb_db` container on the first
  controller; the liveness probe delegates to each compute and runs
  `ovn-appctl` inside its `ovn_controller` container.

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
  disrupted earlier. openstack06 (pure Ceph) is first; openstack01 (cephadm
  bootstrap + mgr + bond0 hazard) is last. No play consumes this yet: it is
  documented data for the future rolling-reboot play (a structure test keeps
  it consistent until then), and today's `upgrade.yml` still walks plain
  inventory order.

Current blocking hazard: **openstack01 `bond0_boot_failure`** — bond0 does not
come up automatically after reboot and needs a manual `systemctl restart
networking`, so it must never be rebooted unattended.

## Overriding (and why it is dangerous)

| Override | Effect | Danger |
| --- | --- | --- |
| `health_gate_<name>_enabled=false` | Skips that check entirely. | You disrupt the fleet without knowing that subsystem is safe. Only skip a check whose subsystem is genuinely out of scope (e.g. OVN on a Ceph-only maintenance with no network disruption). |
| `health_gate_ceph_allow_warn=true` | Accepts `HEALTH_WARN` and proceeds (refused by default). | Only set it after reading the warning and confirming it is benign (e.g. a lone noout flag). `ok-to-stop` still runs, but the health string may hide problems it cannot see. |
| `health_gate_ack_hazards=true` | Proceeds past a blocking hazard. | You are taking responsibility for recovering, e.g. bringing bond0 back up by hand on openstack01. Never set this in an unattended/cron context. |
| `health_gate_galera_container` / `health_gate_rabbitmq_container` | Override container names. | Wrong names make `docker exec` fail; the gate then fails closed (safe). |

Overriding never makes the fleet safer — it only suppresses the warning. The
gate exists because a blind sleep already proved insufficient.

## Credentials

The role reads no secrets from the repo. The Galera query runs inside the
`mariadb` container over the local unix-socket root login Kolla configures
(`mysql` with no password). If a fleet disables socket auth, supply a
credentialed command at runtime — never commit the secret.

## File structure

```
ansible/roles/health-gate/
  defaults/main.yml   # toggles, container names, retries, hazard knobs
  tasks/
    main.yml          # orchestrator (hazards, galera, rabbitmq, ceph, ovn)
    hazards.yml       # consult host_hazards; refuse blocking hazards
    galera.yml        # wsrep Synced + cluster size, every node
    rabbitmq.yml      # no partitions + full membership, every node
    ceph.yml          # health acceptable + ok-to-stop for target host OSDs
    ovn.yml           # chassis liveness, no stale chassis
```

# Rolling reboot playbook

Runbook for `ansible/playbooks/reboot.yml`, the attended rolling reboot used
to activate a patched kernel across the OpenStack/Ceph fleet.

## Scope

The play walks `managed_hosts` in inventory order. That inventory order is
validated against each host's `host_hazards[*].canary_tier`:

1. `openstack06` - pure Ceph canary
2. `openstack05` - compute + Ceph, no controller
3. `openstack04` - compute/controller
4. `openstack02` - compute/controller
5. `openstack01` - Ceph bootstrap + known bond0 recovery hazard

Hosts already running the newest installed kernel are not rebooted again,
so the campaign is resumable by rerunning the playbook. Before such a host is
logged as skipped, the play still checks that every enabled Kolla container
unit's container is running on it (see Aborted campaign).

## Pre-flight

From the repo root:

```bash
./ansible/scripts/validate.sh
```

Review host order without touching hosts:

```bash
cd ansible
ansible-playbook playbooks/reboot.yml --list-hosts
```

Live runs need the Galera root password (health-gate) from Kolla's vaulted
passwords file; `--ask-vault-pass` prompts for the vault key.

Pre-flight dry run: nothing changes on a host before you type `GO` at its
first prompt (the Ceph flags, Alertmanager silence, and reboot all come
after it). So you can run the canary command below and answer anything but
`GO` to exercise the health gate, the Ceph mon/OSD `ok-to-stop` checks, and
the container baseline. The abort adds a `status=aborted` line to the
report log. `--check` is not a usable dry run for this playbook: its probes
are plain `command` tasks that check mode skips.

First live canary:

```bash
cd ansible
ansible-playbook playbooks/reboot.yml --limit openstack06 \
  -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

Full attended campaign:

```bash
cd ansible
ansible-playbook playbooks/reboot.yml \
  -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

## What the play does per host

1. Compares `uname -r` with the newest installed `/boot/vmlinuz-*`, and lists
   the enabled `kolla-<container>-container.service` units. A compute or
   controller host with no such unit stops the play: the container check
   would otherwise compare almost nothing.
2. Runs the `health-gate` role before disruption. Beyond Galera, RabbitMQ,
   OVN chassis liveness and `ceph osd ok-to-stop`, it runs
   `ceph mon ok-to-stop` for the host's monitors and checks OVN NB/SB raft
   `cluster/status` on every controller. `HEALTH_WARN` still needs
   `-e health_gate_ceph_allow_warn=true`, and even then `MON_DOWN`,
   `MON_NETSPLIT`, `OSD_DOWN`, `OSD_HOST_DOWN`, `OSD_ROOT_DOWN`,
   `PG_AVAILABILITY` or `PG_DEGRADED` fail the gate.
3. Records running VMs from `docker exec nova_libvirt virsh list --name --state-running`.
4. Records the running containers that must come back: every container
   backed by an enabled Kolla systemd unit (Kolla runs its containers with
   Docker restart policy `no`; systemd starts them at boot), plus non-Kolla
   containers with a Docker restart policy. Ephemeral check containers and
   per-network `ovnmeta` metadata proxies are excluded. The post-reboot
   check waits until every recorded container is running again, by name.
   (Before this fix the baseline counted only restart-policy containers, so
   the 2026-10-22 campaign logged `pre_containers=1`.)
5. Prompts for operator go/no-go.
6. Only after `GO`: for OSD hosts, sets `ceph osd set noout` and
   `ceph osd set norebalance`. An answer other than `GO` leaves the cluster
   untouched.
7. Creates a best-effort Alertmanager silence for the host (matching its
   `instance` label by name or IP, 2 h default via `reboot_silence_duration`).
   A failed silence only warns — it never blocks the reboot.
8. Reboots with a 20 minute timeout.
9. Verifies kernel, `bond0` state/MTU, hazard-specific state (a single NUMA
   node on `openstack04`, the `xe` driver and SR-IOV VF count on
   `openstack05`), containers, controller health/API, and VM recovery.
   On every compute or controller host it re-runs `health-gate` for OVN
   (chassis connected, NB/SB raft members healthy, polled about 2 min), and
   on controllers also for Galera and RabbitMQ.
   The controller Galera re-check polls each node in turn (up to 10 min per
   node) until it is Synced/Primary/ready at full size, since peers report
   the old cluster size for a few seconds after the rebooted node rejoins. If
   it never converges, the play stops with each node's state and size.
10. For OSD hosts, waits up to 20 min for PG recovery while the Ceph flags
    are still set (from `ceph status`). Every PG must be active, either
    clean or backfilling, with only `clean`, `remapped`, backfill, scrub, or
    snaptrim state flags: remapped/backfilling PGs (a drain, a balancer
    move, or the reboot) still hold every replica and are stalled by
    `norebalance`, so they do not block. Degraded, undersized, peered,
    laggy, unfound, or unknown PGs, or a pgmap whose state counts do not add
    up to the PG total, do. A
    timeout aborts with the flags still set (see Aborted campaign). Then
    unsets the flags, re-runs `ceph cephadm check-host` (a periodic check
    that landed mid-reboot leaves the host Offline with
    `CEPHADM_HOST_CHECK_FAILED`), and waits up to 20 min for Ceph health no
    worse than before the reboot with the same PG check. "No worse" means
    both the status and the set of health check codes: any code that was
    not present at the pre-reboot gate (for example a `MON_DOWN` while the
    baseline was an accepted `HEALTH_WARN`) keeps the wait going. The
    cephadm host re-check and this final health wait run for every
    `ceph_cluster` host, including one with only a monitor or mgr; the PG
    wait and flag handling stay OSD-host only. A sticky
    new code such as `RECENT_CRASH` (a daemon crashed during shutdown) also
    holds the wait and, after 20 min, aborts: read it with
    `cephadm shell -- ceph crash ls-new`, archive it once understood
    (`ceph crash archive-all`), then rerun.
    Expires the Alertmanager silence and appends a summary line to
    `reports/rolling-reboot-2026-07-09.log`.
11. Prompts before continuing to the next host.

Every `command`/`shell` task has a per-attempt `timeout`
(`reboot_command_timeout_seconds`, default 300 s; `health_gate_command_timeout_seconds`
in the role), so a wedged `dockerd` or a hung `cephadm shell` fails the
host instead of stalling the campaign. A timed-out attempt is not retried.
The operator prompts have no timeout.

## Prompts

Type `GO`, `g`, `yes`, or `y` to continue at each prompt. Any other answer
fails closed and stops the campaign.

The first prompt appears before rebooting a host and displays hazards, VM
count, target kernel, and Ceph OSDs. The second prompt appears after that host
has passed post-reboot verification.

## Recovery paths

### Aborted campaign

Any abort — a non-`GO` answer at a prompt, or a failed verification — stops
the campaign with a `status=aborted` line in the report log. A non-`GO` at
the first prompt happens before the flags are set and leaves Ceph untouched.
If the abort happened between setting and unsetting the Ceph flags (after
`GO`), `noout`/`norebalance` may still be set; the failure message repeats
these instructions. Verify and,
once all OSDs are up and `ceph -s` shows no degraded or undersized PGs
(remapped/backfilling PGs are fine), clear them on the Ceph bootstrap host:

```bash
cephadm shell -- ceph osd dump | grep flags
cephadm shell -- ceph osd unset noout
cephadm shell -- ceph osd unset norebalance
```

The play deliberately does not auto-unset on failure: if the host is still
down, unsetting `noout` would start recovery I/O on the degraded cluster.
Rerunning the playbook resumes the campaign (already-rebooted hosts are not
rebooted again). On the rerun each such host still gets a container check:
every enabled `kolla-*-container.service` unit's container must be running
(polled up to 10 min) before the host is logged as skipped. A container
that is enabled but deliberately stopped fails this check by name; start it
or disable its unit. If the abort came after the reboot, the rerun does not
repeat that host's other remaining steps (flag unset, cephadm host check,
final Ceph wait, VM and OVN checks): clear the flags as above, run
`health-check.yml` against the host (see Checks-only playbook), and clear any
stale cephadm warning (see below) before rerunning. An abort also leaves the host's Alertmanager silence in place; it
expires on its own after `reboot_silence_duration`.

### Stale `CEPHADM_HOST_CHECK_FAILED`

If cephadm's periodic host check ran while a host was rebooting, `ceph
health detail` reports `1 hosts fail cephadm check` (connection refused or
no route to host) and `ceph orch host ls` shows the host `Offline` after it
is back. The play re-checks automatically; after an abort, re-check by hand
on the Ceph bootstrap host and the warning clears within about a minute:

```bash
cephadm shell -- ceph cephadm check-host <host>
```

If the check still fails, read its output: it reports either a
connectivity problem (SSH from the mgr) or a host prerequisite failure
(container runtime, time sync, hostname). Fix that before continuing.

### Reboot timeout (firmware updates)

A reboot that also flashes firmware can exceed the 20 minute default. Prefer
raising the timeout up front for such hosts:

```bash
ansible-playbook playbooks/reboot.yml --limit <host> \
  -e reboot_timeout_seconds=3600 \
  -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

If the timeout already fired and the host later booted the new kernel on its
own: clear the Ceph flags as above, then rerun the playbook. The host will be
logged as `skipped` (kernel already current) after the Kolla container check
above passes, but the rest of its per-host verification (bond0, hazards,
VMs, OVN) does not run. Run the checks-only playbook against that host
before letting the campaign continue:

```bash
cd ansible
ansible-playbook playbooks/health-check.yml --limit <host> \
  -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

### Checks-only playbook

`ansible/playbooks/health-check.yml` is the verification half of the campaign
without any disruption: health-gate, newest-kernel assert, `bond0` state/MTU,
hazard-specific checks (NUMA node count, GPU SR-IOV), an assert that no
persistent container is exited or restarting, VM count, and the deploy-host
API smoke. Its container assert still uses the old Docker restart-policy
filter, so it does not see Kolla containers (restart policy `no`); rely on
the rerun of `reboot.yml` for the Kolla container check. Run it any time — after a manually recovered reboot, or against
hosts whose in-playbook verification was skipped on a resumed campaign.

### openstack01 bond0

`openstack01` carries the `bond0_boot_failure` hazard. If it does not return
within the reboot timeout, the play pauses instead of failing immediately.

Use console access:

```bash
sudo systemctl restart networking
ip -j link show bond0
```

Confirm `bond0` is `UP` and MTU is `9000`, then type `GO` in the Ansible
prompt so the play retries SSH connectivity.

### VM recovery timeout

Compute hosts must recover the pre-reboot VM set. If the poll times out, the
play reports the missing VM names and stops. Investigate Nova/libvirt on that
host before continuing.

### Ceph risk window

For OSD hosts, `noout` and `norebalance` keep the disruption window short.
The `volumes-sata-ssd` pool is size 2 / min_size 1, so affected PGs have no
redundancy while an OSD is down. Do not continue to the next host until Ceph
health is no worse than before the reboot and no PG is degraded, undersized,
or inactive. Remapped/backfilling PGs may still be moving; they hold every
replica.

### Slow OSD restart

`openstack06` is the smallest RAM host and may start OSDs slowly after reboot.
Let the Ceph wait loop finish; do not clear the prompt early by starting the
next host manually.

## Notes

Nova is configured with `resume_guests_state_on_host_boot = True`, so the
play reboots in place and polls libvirt for VM recovery. It deliberately does
not live-migrate guests; GPU-passthrough VMs on `openstack04`/`openstack05`
cannot migrate.

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

Hosts already running the newest installed kernel are skipped and logged, so
the campaign is resumable by rerunning the playbook.

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

1. Compares `uname -r` with the newest installed `/boot/vmlinuz-*`.
2. Runs the `health-gate` role before disruption.
3. Records running VMs from `docker exec nova_libvirt virsh list --name --state-running`.
4. Records running container names with `docker ps`, keeping only containers
   whose Docker restart policy is not `no` (ephemeral check containers are
   excluded) and dropping per-network `ovnmeta` metadata proxies. The
   post-reboot check waits until every recorded container is running again,
   by name.
5. For OSD hosts, sets `ceph osd set noout` and `ceph osd set norebalance`.
6. Prompts for operator go/no-go.
7. Creates a best-effort Alertmanager silence for the host (matching its
   `instance` label by name or IP, 2 h default via `reboot_silence_duration`).
   A failed silence only warns — it never blocks the reboot.
8. Reboots with a 20 minute timeout.
9. Verifies kernel, `bond0` state/MTU, hazard-specific state (a single NUMA
   node on `openstack04`, the `xe` driver and SR-IOV VF count on
   `openstack05`), containers, controller health/API, and VM recovery.
10. For OSD hosts, waits for all PGs to return to `active+clean` while the
    Ceph flags are still set (an abort here keeps the degraded cluster
    protected), then unsets the flags, then waits for the pre-reboot health
    status. Expires the Alertmanager silence and appends a summary line to
    `reports/rolling-reboot-2026-07-09.log`.
11. Prompts before continuing to the next host.

## Prompts

Type `GO`, `g`, `yes`, or `y` to continue at each prompt. Any other answer
fails closed and stops the campaign.

The first prompt appears before rebooting a host and displays hazards, VM
count, target kernel, and Ceph OSDs. The second prompt appears after that host
has passed post-reboot verification.

## Recovery paths

### Aborted campaign

Any abort — a non-`GO` answer at a prompt, or a failed verification — stops
the campaign with a `status=aborted` line in the report log. If the abort
happened between setting and unsetting the Ceph flags, `noout`/`norebalance`
may still be set; the failure message repeats these instructions. Verify and,
once the host's OSDs are back up, clear them on the Ceph bootstrap host:

```bash
cephadm shell -- ceph osd dump | grep flags
cephadm shell -- ceph osd unset noout
cephadm shell -- ceph osd unset norebalance
```

The play deliberately does not auto-unset on failure: if the host is still
down, unsetting `noout` would start recovery I/O on the degraded cluster.
Rerunning the playbook resumes the campaign (already-rebooted hosts are
skipped). An abort also leaves the host's Alertmanager silence in place; it
expires on its own after `reboot_silence_duration`.

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
logged as `skipped` (kernel already current) — which also means its per-host
verification (bond0, containers, VMs) does not run. Run the checks-only
playbook against that host before letting the campaign continue:

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
API smoke. Run it any time — after a manually recovered reboot, or against
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
returns to the pre-reboot health state and PGs are `active+clean`.

### Slow OSD restart

`openstack06` is the smallest RAM host and may start OSDs slowly after reboot.
Let the Ceph wait loop finish; do not clear the prompt early by starting the
next host manually.

## Notes

Nova is configured with `resume_guests_state_on_host_boot = True`, so the
play reboots in place and polls libvirt for VM recovery. It deliberately does
not live-migrate guests; GPU-passthrough VMs on `openstack04`/`openstack05`
cannot migrate.

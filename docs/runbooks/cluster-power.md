# Full-cluster shutdown and cold start

Runbook for `ansible/playbooks/cluster-shutdown.yml` and
`ansible/playbooks/cluster-startup.yml`: power off every managed host
(openstack01, 02, 04, 05, 06) gracefully, for example to replace a switch,
then bring the cluster back. For one host at a time use
[rolling-reboot.md](rolling-reboot.md) instead.

`deploy01` is not powered off. It runs Kolla-Ansible, the openstack CLI, and
Harbor, and the startup playbook delegates to it, so it must be reachable
from the operator machine during startup.

## Before the window

- Announce the outage to users. Every VM is shut down.
- Take a MariaDB backup on the deploy host (backups have been failing, so
  confirm it actually succeeded):
  `kolla-ansible mariadb-backup -i /etc/kolla/multinode --vault-password-file /etc/kolla/ansible_vault_pass`.
- Confirm console or BMC access to every host, especially `openstack01`
  (`bond0_boot_failure` hazard), and that you can power hosts on remotely
  or in person.
- Know which networks the switch carries. Ansible reaches hosts over
  `eno1`/`eno8303` on 192.168.0.0/24. Ceph, OVN tunnels, the API, and VIPs
  run on the 25 GbE `bond0` (LACP) and its VLANs 100, 101, 1113, 1115,
  2116, and 2240, at MTU 9000. The new switch needs the same port-channels,
  VLAN trunks, and jumbo MTU.
- Optional: stop the deploy-host timers that talk to the cloud
  (`systemctl list-timers` on deploy01). They fail harmlessly during the
  outage, but cert renewal would run `kolla-ansible reconfigure` against
  hosts that are down.
- Make sure no live migration is running
  (`openstack server migration list --status running`). A migration that
  libvirt finishes after `nova_compute` is held leaves the guest on a host
  Nova does not know about.
- Right before you start, check that no long storage job is running:
  volumes in `creating`, `downloading`, `uploading` or `backing-up`
  (`openstack volume list --all-projects --long`), or images in `saving` or
  `importing`. Fencing the API stops new jobs but does not drain running
  ones.
- Lint: `./ansible/scripts/validate.sh`.

Both playbooks need the Galera password for the health gate:
`-e @/path/to/kolla/passwords.yml --ask-vault-pass`.
Neither accepts `--limit`, and `--check` is not a usable dry run: their
probes are `command` tasks, which check mode skips. Answering anything but
`GO` at the first shutdown prompt is the dry run. It runs the health gate
without changing anything or leaving a state file behind.

## Shutdown

```bash
cd ansible
ansible-playbook playbooks/cluster-shutdown.yml \
  -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

1. **Pre-flight.** Runs the `health-gate` role once: Galera, RabbitMQ, OVN
   chassis and raft, and Ceph health with `ok-to-stop`. `HEALTH_WARN` needs
   `-e health_gate_ceph_allow_warn=true`. It also reads the Ceph flags
   that are already set.
2. **GO prompt.** Nothing has changed yet.
3. **Silence and state.** Creates one Alertmanager silence
   (`alertname=~".+"`, 12 h, `cluster_power_silence_duration`). This step is
   best-effort. The silence persists through the outage and startup expires
   it. Then it writes `reports/cluster-power-state.json` (gitignored) with
   the existing Ceph flags and the silence id, before anything else changes,
   so that `cluster-startup.yml` can always undo the run.
4. **Hold units.** First stops and disables `haproxy` on every controller.
   Only after that does it stop `nova_compute` on the computes and
   `octavia_health_manager` on the controllers.
   - With haproxy down, every API and the RGW S3 frontend are fenced, so no
     new request changes a VM or writes to Ceph. This is when users lose
     the API.
   - Nova never sees the guests stop, so they stay `ACTIVE` in its
     database. Nova's `resume_guests_state_on_host_boot = True` restarts
     them when startup re-enables `nova_compute`.
   - The health manager is held so it does not start amphora failovers
     once amphorae go silent.
5. **VMs.** Records the running VMs on each compute host in the state file,
   now that the API is fenced and no new migration can start.
6. **Guests.** Runs `virsh shutdown` (ACPI) on every active domain and
   waits up to 10 min (`cluster_power_guest_shutdown_timeout_seconds`).
   Active domains include paused ones, which never answer ACPI and are not
   restored at startup. Guests still active are listed. GO force-stops them
   with `virsh destroy`; any other answer aborts, and a rerun retries the
   graceful shutdown. Before Ceph is touched, every compute must then show
   an empty domain list.
7. **Ceph flags.** Sets `noout`, `norecover`, `norebalance`, and
   `nobackfill`. It leaves out `nodown` and `pause`: every client is already
   off, and `nodown` would hide an OSD that fails to come back (see
   [Croit][croit]). [IBM's procedure][ibm] sets all six.
8. **Ceph re-check.** Runs before MariaDB stops, so a failure leaves Galera
   intact: no OSD down and no degraded or unavailable PG. The flags'
   `HEALTH_WARN` is accepted.
9. **MariaDB.** Stops and disables the `mariadb` unit on openstack04, then
   02, then 01, so each node leaves Galera cleanly. Disabling it means it
   does not crash-loop at boot without a primary component.
10. **Second GO prompt**, then power-off of all five hosts in parallel, and
    a wait for SSH to close. A line is appended to `reports/cluster-power.log`.

SSH closes before the host is fully off. Before you unplug anything,
confirm on the BMC or console that each host is powered off.

### If the shutdown stops partway

The hosts are still up. Either fix the cause and rerun
`cluster-shutdown.yml` (it reuses the existing state file, so VMs already
shut down are still restored later), or undo everything with
`cluster-startup.yml`. Startup handles a partial shutdown too: it unsets
only the flags the shutdown added, and if MariaDB was stopped on only some
controllers, `mariadb-recovery` stops the rest before recovering.

## Switch work

Swap the switch. Before you power any host on, check that it is up and
stable, with port-channels, VLANs, and MTU configured as described in
Before the window.

## Power-on

Power on all five hosts (BMC or front panel). Order does not matter: Ceph
mons need any two of openstack01/02/04, and MariaDB waits for recovery.
`nova_compute`, `haproxy`, the Octavia health manager, and MariaDB stay
down because their units are disabled.

If `openstack01`'s `bond0` does not come up, run
`systemctl restart networking` on it. The host is still reachable over
`eno1`. The startup bond0 check names the hazard if you miss it.

## Startup

```bash
cd ansible
ansible-playbook playbooks/cluster-startup.yml \
  -e @/path/to/kolla/passwords.yml --ask-vault-pass
```

1. **Reachability.** Waits up to 30 min for SSH on every host.
2. **Network.**
   - Every `bond0*` interface must be `UP` at MTU 9000. That includes the
     unnumbered provider VLANs 2116 and 2240.
   - Every host must have the same set of `bond0*` interfaces and the same
     IPv4-addressed ones.
   - Every host then sends don't-fragment pings at full MTU to every peer
     on each addressed `bond0*` interface (keepalived /32 VIPs are
     skipped). A failure lists the `iface->peer` pairs. Check the switch's
     VLAN trunk membership, LACP port-channel state, and port MTU.
   - The provider VLANs have no host address, so the pings cannot cover
     them. Once startup finishes, ping a known floating IP on each of them
     from outside.
3. **Ceph.**
   - Waits up to 20 min for full mon quorum and for every OSD that is `in`
     to be `up` (an OSD drained before the shutdown is ignored).
   - Re-runs `ceph cephadm check-host` on every host and unsets only the
     flags the shutdown added.
   - Finally the health gate polls up to 10 min for acceptable health and
     `ok-to-stop`. If flags were already set before the shutdown,
     `HEALTH_WARN` is accepted. Down OSDs and degraded PGs still fail either
     way.
   - A `RECENT_CRASH` from the shutdown also blocks: read it with
     `ceph crash ls-new`, then `ceph crash archive-all`.
4. **MariaDB prompt.** GO runs
   `kolla-ansible mariadb-recovery -i /etc/kolla/multinode` on deploy01
   ([Kolla docs][kolla-recovery]). It stops the mariadb containers, runs
   `--wsrep-recover` on each node, and bootstraps from the node with the
   highest seqno. Answer `SKIP` on a rerun if Galera is already up. The
   mariadb unit is then re-enabled either way.
5. **Restart Kolla units.** Runs `systemctl reset-failed` and starts every
   enabled Kolla unit. The held units are left out, even on a rerun that
   finds them already enabled. This is a no-op for running units and restarts services that
   gave up while the database was down. RabbitMQ starts on all three
   controllers at once.
6. **Control-plane gate.** Waits for Galera to be Synced/Primary at size 3,
   then checks RabbitMQ (Khepri, all 3 nodes running) and OVN chassis and
   raft. These checks run inside the containers, so they do not need the
   VIPs.
7. **Release units and guests.**
   - Starts and enables `haproxy`, then polls `openstack endpoint list` from
     deploy01 until the API answers. keepalived only holds the VIPs,
     including the database VIP, while haproxy runs. That is why the API
     comes back before the rest.
   - Checks that every enabled `kolla-*-container.service` container is
     running (the same check as the rolling reboot).
   - Starts `nova_compute` and polls up to 40 min until every VM recorded at
     shutdown is running again.
   - Waits a 5 min grace period (`cluster_power_amphora_boot_grace_seconds`,
     on every run) for amphorae to boot and heartbeat, then enables and
     starts `octavia_health_manager`.
   - Finally, checks that every held unit is active.
8. **Finish.** Expires the silence. If the expiry fails it warns, and every alert stays muted until
   the silence lapses, so expire it by hand. Then it renames the state file
   to `reports/cluster-power-state-<timestamp>.json` and logs a line.

Both playbooks stop if a host drops out of the play (for example by losing
SSH), instead of carrying on with the rest of the fleet.

Every step is safe to rerun. When the playbook stops, fix the cause and
rerun it from the start.

### Unplanned outage (no state file)

Startup refuses to run without `reports/cluster-power-state.json`. After a
power loss, create it with
`{"created": "manual", "vms": {}, "preexisting_ceph_flags": [...], "silence_id": ""}`.
In `preexisting_ceph_flags`, list every flag that
`cephadm shell -- ceph osd dump | grep flags` shows, so that startup keeps
them all and unsets nothing. Then rerun. This skips the VM recovery check; check `openstack server list`
by hand. Galera may come back on its own after a crash (`pc.recovery`);
answer `SKIP` at the MariaDB prompt if it is already Synced.

## Notes

- GPU-passthrough VMs on openstack04/05 are handled like any other guest.
  The startup network check does not cover the SR-IOV and NUMA hazards, so
  run `health-check.yml` afterwards if those hosts matter to you.
- VMs that were already shut off before the shutdown stay off.
- Startup always re-enables the held units. If `nova_compute`, `haproxy`,
  or the Octavia health manager was deliberately disabled on a host before
  the window, disable it again afterwards.

[ibm]: https://www.ibm.com/docs/en/storage-ceph/8.0.0?topic=cluster-powering-down-rebooting-that-uses-ceph-orchestrator
[croit]: https://www.croit.io/blog/how-not-to-shut-down-a-ceph-cluster
[kolla-recovery]: https://docs.openstack.org/kolla-ansible/latest/user/operating-kolla.html

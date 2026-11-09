# Cinder active-active cluster (`cinder_cluster_name`)

cinder-volume runs on two hosts (`[cinder-volume:children] storage`, which is
openstack01 and openstack02 in `kolla/multinode`) with no cluster configured.
That causes two problems:

- **The kolla prechecks fail.** kolla-ansible 2026.2 asserts
  `cinder_cluster_name != ""` when the `cinder-volume` group has more than one
  host ([`roles/cinder/tasks/precheck.yml#L117-L127`][ka-precheck]).
- **Each volume is owned by one host.** A volume's `host` is
  `openstackNN@<backend>#<pool>`, and the API sends its RPCs to that one
  service. If that host's `cinder_volume` container is down, the volume
  cannot be deleted, extended, snapshotted, or retyped, even though the
  other host reaches the same Ceph pools.

Until this runbook is done, `README.md` / `README.en.md` run the prechecks
with `-e '{"cinder_cluster_skip_precheck": true}'`. That switch only gates
the precheck ([`precheck.yml#L126,L139`][ka-precheck]) and changes no
rendered config.

## What setting it does

- Kolla renders `cluster = <name>` into `[DEFAULT]` of cinder-volume's
  `cinder.conf` only ([`templates/cinder.conf.j2#L23-L25`][ka-tmpl]). Cinder
  turns it into one cluster per backend: `<name>@rbd-nvme` and
  `<name>@rbd-sata-ssd` ([`cinder/cmd/volume.py#L80-L83`][c-cmd]).
- When cinder-volume restarts and its `services` row has no `cluster_name`,
  it creates the cluster, records itself as a member, and moves every
  volume, consistency group, group, and image-cache entry whose `host` is
  that service into the cluster by setting `cluster_name`
  ([`cinder/service.py#L170-L192`][c-svc],
  [`volume/manager.py#L463-L482`][c-mgr]; documented in the
  [Cinder HA contributor guide, "Cinder-Volume"][c-ha]). `host` is not
  changed.
- From then on the API sends a volume's RPCs to `cluster_name` when set,
  otherwise to `host` ([`objects/base.py#L523-L524`][c-base]), so either
  member can serve any volume in the cluster.
- This is automatic only the **first** time. Cinder does nothing if the
  cluster name is later changed ([`service.py#L172-L177`][c-svc]), so pick
  the name once.

Why this is not committed in `globals.yml`: the move is automatic, but only
for resources whose `host` matches a service that restarts with the new
setting. Volumes left on a dead or renamed service (an old host, or a legacy
`backend_host` such as `rbd:volumes`) would stay outside the cluster. Those
have to be found and fixed by hand first, and there is no clean automatic
rollback (see [Rollback](#rollback)). Both need an operator.

## Prerequisites (all must hold)

| Requirement | Evidence / how to check |
| --- | --- |
| The driver supports active-active | RBD: `SUPPORTS_ACTIVE_ACTIVE = True` ([`drivers/rbd.py#L305`][c-rbd]); support matrix `active_active_ha: driver.rbd=complete` ([`support-matrix.ini#L1061`][c-matrix]) |
| A coordination (DLM) backend is configured | kolla 2026.2 sets `cinder_coordination_backend` to `valkey` when `enable_valkey` is on ([`group_vars/all/cinder.yml#L84`][ka-coord]), and `globals.yml` has `enable_valkey: "yes"`. Check: `grep -A1 '^\[coordination\]' /etc/kolla/cinder-volume/cinder.conf` on both hosts shows a `backend_url`. |
| Both members have identical backend config | Both hosts render from the same `kolla/config/cinder.conf` (`rbd-nvme`, `rbd-sata-ssd`). |
| The name is unique | It must not equal any `host` or `backend_host` value ([Cinder HA guide warning][c-ha]). This runbook uses `ceph-rbd`. |

## Steps

Run the `openstack` / `cinder` commands with admin credentials on the deploy
host. Run `cinder-manage` inside a `cinder_api` container
(`docker exec cinder_api cinder-manage ...` on any controller).

### 1. Pre-flight (read-only)

```sh
# Services and their current cluster (expect Cluster empty for all).
docker exec cinder_api cinder-manage service list

# Every non-deleted volume's owning host and status.
cinder --os-volume-api-version 3.61 list --all-tenants \
  --fields os-vol-host-attr:host,status,cluster_name

# Host distribution (should contain only openstack01@... and openstack02@...).
cinder list --all-tenants --fields os-vol-host-attr:host \
  | awk -F'|' 'NR>3 && NF>2 {print $3}' | sed 's/#.*//' | sort | uniq -c
```

Stop and fix before continuing if:

- **A volume's host is not a live cinder-volume service** (a decommissioned
  host, `rbd:volumes@...`, a typo). Move it to a live service first, for
  example:

  ```sh
  docker exec cinder_api cinder-manage volume update_host \
    --currenthost OLDHOST@rbd-nvme#rbd-nvme \
    --newhost openstack01@rbd-nvme#rbd-nvme
  ```

  ([`cinder/cmd/manage.py#L623-L639`][c-manage]). Then remove the dead
  service with `cinder-manage service remove cinder-volume OLDHOST@rbd-nvme`.
- **Any volume is in a transitional state** (`creating`, `deleting`,
  `attaching`, `detaching`, `extending`, `migrating`, `retyping`,
  `backing-up`, `restoring-backup`, `downloading`, `uploading`). Wait for it
  to settle.
- **The `[coordination]` check above shows no `backend_url`.**

### 2. Set the name

In `kolla/globals.yml`, replace the commented line with:

```yaml
cinder_cluster_name: "ceph-rbd"
```

Commit and pull on the deploy host (`/etc/kolla` symlinks into the repo).

```sh
kolla-ansible -i kolla/multinode --configdir kolla prechecks --tags cinder
```

This must now pass without `cinder_cluster_skip_precheck`.

### 3. Roll out one host at a time

```sh
kolla-ansible -i kolla/multinode --configdir kolla reconfigure --tags cinder --limit openstack01
```

Check on openstack01:

```sh
grep -E '^cluster *=' /etc/kolla/cinder-volume/cinder.conf    # cluster = ceph-rbd
grep 'have been included in cluster' /var/log/kolla/cinder/cinder-volume.log
docker exec cinder_api cinder-manage cluster list   # ceph-rbd@rbd-nvme, ceph-rbd@rbd-sata-ssd: 1 host, 0 down
```

The log line reports how many volumes moved per backend. It should match the
pre-flight count for openstack01. Then repeat with `--limit openstack02`.
After the second host, `cluster list` shows 2 hosts and 0 down for each
cluster.

### 4. Verify

```sh
docker exec cinder_api cinder-manage service list   # every cinder-volume row has a Cluster
cinder --os-volume-api-version 3.61 list --all-tenants --fields os-vol-host-attr:host,cluster_name
```

Every volume should have a `cluster_name` of `ceph-rbd@<backend>#<pool>`.
Then smoke-test: create a 1 GiB volume, attach it to a test VM, detach it,
and delete it. Optionally, stop `cinder_volume` on one host, delete or extend
a test volume whose `host` is that node, confirm it succeeds through the
other node, and restart the container.

### 5. Clean up

Remove `-e '{"cinder_cluster_skip_precheck": true}'` from the prechecks
command in `README.md` and `README.en.md`. Rewrite the `globals.yml` comment
above `cinder_cluster_name` so it describes the live setting instead of
saying "do not uncomment".

## Rollback

**Before step 3:** revert the `globals.yml` commit. Nothing has changed in
the database.

**After step 3:** removing `cinder_cluster_name` and reconfiguring does not
undo anything. The services keep their `cluster_name`, the volumes keep
theirs, and RPCs keep going to a cluster topic that nobody is consuming any
more, so volume operations time out. Fixing forward (repairing the cluster)
is the preferred path. If you really must roll back:

1. Revert `cinder_cluster_name` and run
   `kolla-ansible ... reconfigure --tags cinder`.
2. For each backend, clear the volumes' and consistency groups' cluster
   (`cinder-manage cluster rename` with an empty new name sets `cluster_name`
   to NULL; [`manage.py#L852-L893`][c-manage], [`cinder/db/api.py#L2175-L2212`][c-dbapi]):

   ```sh
   docker exec cinder_api cinder-manage cluster rename ceph-rbd@rbd-nvme ''
   docker exec cinder_api cinder-manage cluster rename ceph-rbd@rbd-sata-ssd ''
   ```

   `rename` takes only `current new` (no binary argument). A backend-level
   name like `ceph-rbd@rbd-nvme` also matches the stored
   `ceph-rbd@rbd-nvme#<pool>` values ([`db/api.py#L697-L723`][c-filter]).
   `rename` does not touch generic groups or image-volume-cache entries. If
   the pre-flight showed any, clear their `cluster_name` in the database by
   hand.
3. Remove each cluster and its member service rows
   (`cinder-manage cluster remove --recursive cinder-volume ceph-rbd@rbd-nvme`,
   the same for `rbd-sata-ssd`), then restart `cinder_volume` on both hosts.
   The services recreate their rows without a cluster
   ([`service.py#L194-L200`][c-svc]).
4. Re-run the pre-flight queries. Every volume should have an empty
   `cluster_name` and a live `host`.

[ka-precheck]: https://opendev.org/openstack/kolla-ansible/src/branch/stable/2026.2/ansible/roles/cinder/tasks/precheck.yml#L117-L140
[ka-tmpl]: https://opendev.org/openstack/kolla-ansible/src/branch/stable/2026.2/ansible/roles/cinder/templates/cinder.conf.j2#L23-L25
[ka-coord]: https://opendev.org/openstack/kolla-ansible/src/branch/stable/2026.2/ansible/group_vars/all/cinder.yml#L84
[c-cmd]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/cmd/volume.py#L80-L83
[c-svc]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/service.py#L170-L200
[c-mgr]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/volume/manager.py#L463-L482
[c-base]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/objects/base.py#L523-L524
[c-rbd]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/volume/drivers/rbd.py#L305
[c-matrix]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/doc/source/reference/support-matrix.ini#L1061
[c-manage]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/cmd/manage.py#L623-L893
[c-dbapi]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/db/api.py#L2175-L2212
[c-filter]: https://opendev.org/openstack/cinder/src/branch/stable/2026.2/cinder/db/api.py#L697-L723
[c-ha]: https://docs.openstack.org/cinder/latest/contributor/high_availability.html#cinder-volume

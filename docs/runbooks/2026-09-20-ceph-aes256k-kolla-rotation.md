---
title: Ceph aes256k migration for OpenStack 2026.1 with Kolla-Ansible
type: ops
status: reviewed-draft
date: 2026-09-20
---

# Ceph aes256k migration for OpenStack 2026.1

This runbook records the investigation and the procedure for migrating OpenStack
CephX credentials after the Ceph 20.2.4 upgrade. No rotation or deployment was
performed during the investigation. Execute changes only in an approved
maintenance window after the readiness gates below pass.

## Verified state and scope

Read-only checks on `debian@192.168.0.21` found:

| Check | Observed state |
|---|---|
| Ceph reporting daemons | All 25 on 20.2.4 |
| OSDs | 17 up and in |
| Placement groups | 625 clean, including four undergoing deep scrub |
| Allowed credential ciphers | `aes,aes256k` |
| Preferred new-credential cipher | `aes` |
| Service-ticket cipher | `aes256k` |
| Service-ticket TTL | 3,600 seconds |
| Legacy client identities | 27 |
| Legacy rotating service-key types | Four |
| Insecure key creation | Runtime value `true` on mon.openstack01 |

`glance_api`, `cinder_volume`, `cinder_backup`, `nova_compute`, and `nova_libvirt`
on this host all contain Ceph libraries `19.2.3-0ubuntu0.24.04.3`.
Other hosts have not been checked. No key contents were inspected or displayed.

OpenStack 2026.1 alone does not establish compatibility: the client libraries
inside the service and QEMU environments must support `aes256k`. Upstream fixes
are in Ceph 19.2.6 and 20.2.4. Upgrade the current client images before rotation.
Ubuntu records a 19.2.6 upload to `noble-proposed`; this is not proof that a fixed
package is available in the production repositories used by the image build.
See [Ubuntu's package changelog][ubuntu].

The repository selects Debian images (`kolla_base_distro: debian`, registry
`registry.cloudnative.tw`), whereas the observed packages have Ubuntu suffixes.
Reconcile the actual deployed image origin before rebuilding; the fix differs
completely depending on the answer (repoint `kolla_base_distro` versus repair a
custom Ubuntu-based build). See step 1.

Only the rotating-service-key warning is expected to expire automatically now
that the service-ticket cipher is `aes256k`. Allow several hours; do not wipe the
rotating key database merely to clear the warning. Client credentials and the
legacy-authentication policy require explicit migration. See [Ceph's procedure][ceph].

## Safety and readiness gates

- Keep both authentication ciphers enabled throughout the transition.
- Confirm administrative and emergency recovery access independently of the
  OpenStack credentials. `client.admin` is rotated last, in section 7, never
  before every other consumer is verified.
- Identify every consumer of each credential, including compute hosts, Cinder
  backup workers, configuration overrides, and automation.
- Verify patched client libraries on every relevant host and container.
- Plan how every affected QEMU process will load the updated libraries and key.
  An in-guest reboot or Nova service restart alone is insufficient.
- Reserve time for a canary and all guest transitions; do not rely on a guaranteed
  grace period after rotating an existing identity.
- Keep credential files outside Git, in protected persistent storage. Do not
  print keyrings, use `tee`, enable shell tracing, or use Ansible `--diff` or
  verbose/debug logging during credential deployment.
- Do not use `ceph auth ls`, `auth get`, `auth export`, or libvirt secret-value
  inspection commands to verify the migration. Use health metadata and functional
  checks. Credential transport tools necessarily consume the files; operators
  and diagnostic output do not need to reveal their contents.

## 1. Upgrade and validate the client images

Build or obtain compatible OpenStack 2026.1 images containing patched
`librados2`, `librbd1`, `python3-rados`, `python3-rbd`, and, where installed,
`ceph-common`. Include Nova libvirt, where QEMU uses RBD.

Use the existing reviewed image build, registry, and pinning process. Do not
assume a newer image tag guarantees updated packages or modify running
containers with ad hoc package upgrades.

First reconcile the image origin. On one compute host and one controller,
without reading configuration or environment:

```bash
sudo docker inspect --format '{{index .Config.Labels "build-date"}} {{.Config.Image}}' nova_libvirt
sudo docker exec nova_libvirt cat /etc/os-release
```

Compare the reported base against `kolla_base_distro` and the Harbor build
lineage. Stop until the source of the running images is known.

Run every `kolla-ansible` command in this runbook as the deploy user from its
operator virtualenv, following [ADR-0019](../adr/0019-tls-cert-renewal.md),
not as your own login user. On the deployment host set the reviewed inventory
path. Retain the deployment's normal configuration directory and extra-variable
arguments in all commands below.

```bash
export KOLLA_INVENTORY=/absolute/path/to/kolla/multinode
test -f "$KOLLA_INVENTORY"
kolla-ansible --version
```

After selecting verified images, deploy during the approved window:

```bash
kolla-ansible pull -i "$KOLLA_INVENTORY" --tags glance,cinder,nova
kolla-ansible deploy -i "$KOLLA_INVENTORY" --tags glance,cinder,nova
```

Pulling alone does not update containers. Replacing the `nova_libvirt` image
recreates that container, and in Kolla the QEMU processes live inside it, so
the recreate kills every running guest on the host. The stable/2026.1 restart
handler retries rather than refusing when guests are present. Therefore run
the Nova tag one compute host at a time with `--limit`, only after that host
has been drained by live migration, in the order used by the
[rolling reboot runbook](rolling-reboot.md): openstack05, openstack04,
openstack02, openstack01. See [Kolla operations][operations].

On every relevant host, inspect package versions without accessing credentials:

```bash
sudo docker exec nova_libvirt dpkg-query -W librados2 librbd1
sudo docker exec nova_compute dpkg-query -W librados2 librbd1 python3-rados python3-rbd
sudo docker exec cinder_volume dpkg-query -W librados2 librbd1 python3-rados python3-rbd
sudo docker exec cinder_backup dpkg-query -W librados2 librbd1 python3-rados python3-rbd
sudo docker exec glance_api dpkg-query -W librados2 librbd1 python3-rados python3-rbd
```

Run only for services present on that host. Record versions and image digests,
not full container inspections or environment dumps. Require a fixed upstream
release or documented vendor backport before continuing. Kernel RBD/CephFS
consumers, if any, need separate kernel compatibility verification.

## 2. Select the credential transition strategy

| Strategy | Use when | Tradeoff |
|---|---|---|
| Rotate existing identities | All consumers and VMs can transition in a coordinated window | Few configuration changes, but old credentials fail on subsequent authentication |
| Create replacement identities | Guest transitions require an extended period | Old credentials remain usable, but identity settings and existing VM/volume connection metadata require a tested transition plan |

Prefer replacement identities when the VM transition cannot fit a controlled
window. Create distinct users with equivalent least-privilege caps, deploy their
keyrings and user settings, test existing attached volumes and migration, then
retire the old users after all consumers move. Do not assume changing
`ceph_cinder_user` rewrites existing volume connection metadata.

The executable procedure below covers **in-place rotation only**. Replacement
identities require a deployment-specific extension reviewed before execution.
OpenStack documents both approaches and requires guest migration or restart
to refresh credentials. See [OSSN-0108][ossn].

## 3. Prepare the credential destinations

For the default custom-config root and existing identity names:

| Identity | Deployment-host destination |
|---|---|
| `client.glance` | `/etc/kolla/config/glance/ceph.client.glance.keyring` |
| `client.cinder` | `/etc/kolla/config/cinder/cinder-volume/ceph.client.cinder.keyring` |
| `client.cinder` | `/etc/kolla/config/cinder/cinder-backup/ceph.client.cinder.keyring` |
| `client.cinder` | `/etc/kolla/config/nova/ceph.client.cinder.keyring` |
| `client.cinder-backup` | `/etc/kolla/config/cinder/cinder-backup/ceph.client.cinder-backup.keyring` |

Use the actual `node_custom_config` root and update any per-host overrides.
Nova defaults to the Cinder identity unless overridden. For rotation under the
same identity, preserve the existing libvirt secret UUIDs. Kolla supplies the
new values using those identifiers. See [Kolla external Ceph][external] and
the [Nova credential tasks][nova-tasks].

How the secret reaches libvirt matters. With the default containerized libvirt,
Kolla writes `<uuid>.base64` under the host's `nova-libvirt/secrets` config
directory and notifies **no handler**. The file is copied into the container by
`kolla_set_configs` only when the container starts. A keyring-only reconfigure
therefore leaves the running libvirtd holding the old secret value. Step 5
loads the new value live with `virsh secret-set-value`, so no container restart
and no host drain is needed for the secret itself. Running QEMU processes still
hold the old key in memory and are handled in step 6.

Prepare an approved, persistent staging directory owned by the deployment
operator, mode `0700`, outside the checkout. Verify destinations and deployment
permissions before issuing any rotation. Securely preserve configuration and
image rollback information; restoring an old keyring alone will not undo a
Ceph-side rotation.

## 4. Rotate Glance, then Cinder backup

Ceph changes the credential; Kolla distributes it. Do one identity at a time.
The following Bash example runs on the deployment host, and writes only the
new keyring to a protected file. Set `CEPH_ROTATION_DIR` to the prepared directory.

```bash
export CEPH_ROTATION_DIR=/absolute/protected/path/ceph-rotation
(
  set -e
  set +x
  umask 077
  test -d "$CEPH_ROTATION_DIR"
  # noclobber prevents accidental reuse of this output filename.
  set -o noclobber
  out="$CEPH_ROTATION_DIR/ceph.client.glance.keyring.new"
  ssh -o BatchMode=yes debian@192.168.0.21 \
    'sudo -n cephadm shell -- ceph auth rotate --key-type=aes256k client.glance' \
    > "$out"
  # Shape check only: exactly one stanza header and one key line, value never printed.
  test "$(grep -cE '^\[client\.glance\]$' "$out")" -eq 1
  test "$(grep -cE '^[[:space:]]*key = ' "$out")" -eq 1
  test "$(grep -cvE '^(\[client\.glance\]|[[:space:]]*key = |[[:space:]]*(caps .*)?)$' "$out")" -eq 0
)
```

The third check rejects cephadm log noise or banners that a bare `test -s`
would accept. Substitute the identity name in the header pattern when reusing
the block.

If the command fails, **stop**. A transport failure may occur after Ceph has
already rotated the credential. Do not blindly retry or deploy a partial file;
use the approved secure credential-recovery process without displaying keys.

Install the successful output through the established secret-deployment
mechanism into the Glance destination, maintaining restrictive permissions and
read access for the Kolla deployment operator. Then:

```bash
kolla-ansible reconfigure -i "$KOLLA_INVENTORY" --tags glance
```

Upload and download a disposable image. Verify fresh authentication works.

Repeat the guarded rotation with `client.cinder-backup` and output filename
`ceph.client.cinder-backup.keyring.new`. Deploy it to the backup destination:

```bash
kolla-ansible reconfigure -i "$KOLLA_INVENTORY" --tags cinder
```

Back up and restore a disposable volume before proceeding.

## 5. Rotate the shared Cinder/Nova credential

Begin only when all compute transitions are ready. Pause conflicting image,
volume, backup, and migration operations according to the maintenance plan.

**Why this step is time-critical.** After `ceph auth rotate`, an established
RBD session keeps working until it next needs a ticket. Anything that
re-authenticates after roughly one `auth_service_ticket_ttl` (observed 3,600
seconds) is rejected under the old secret. Four compute hosts cannot be drained
serially inside one hour, so widen the window first.

1. At least one full current TTL before rotating, extend the ticket lifetime and
   confirm the running value on every monitor:

   ```bash
   sudo cephadm shell -- ceph config set mon auth_service_ticket_ttl 172800
   for m in openstack01 openstack02 openstack04; do
     sudo cephadm shell -- ceph config show mon.$m auth_service_ticket_ttl
   done
   ```

   Wait one old TTL (one hour) so every client has refreshed onto a long
   ticket. This buys time; it does not remove the need for step 6.
2. Repeat the guarded rotation for `client.cinder`, writing
   `ceph.client.cinder.keyring.new`.
3. Deploy that **same new credential** to all three Cinder/Nova destinations,
   including any per-host overrides.
4. Reconfigure all consumers, including every compute host. With a keyring-only
   change this restarts `cinder_volume`, `cinder_backup`, and `nova_compute`,
   and rewrites the libvirt secret files, but does not restart `nova_libvirt`:

   ```bash
   kolla-ansible reconfigure -i "$KOLLA_INVENTORY" --tags cinder,nova
   ```

5. Load the new secret value into the running libvirtd on every compute host.
   Read the two UUIDs from `rbd_secret_uuid` and `cinder_rbd_secret_uuid` in the
   vaulted passwords file (only those two fields). The `--file` form reads the
   base64 file directly; the value never appears on a command line or terminal.
   Verify the mounted path on the first host before looping:

   ```bash
   for u in "$RBD_SECRET_UUID" "$CINDER_RBD_SECRET_UUID"; do
     sudo docker exec nova_libvirt virsh secret-set-value --secret "$u" \
       --file "/var/lib/kolla/config_files/secrets/$u.base64"
   done
   sudo docker exec nova_libvirt virsh secret-list
   ```

   `secret-list` shows UUIDs and usage only. Do not run `secret-get-value`.
6. Reset the ticket TTL to 3,600 seconds only after section 6 is complete.

Review successful Kolla completion and service readiness without reading
secrets. A copied file by itself is not proof that libvirtd or running guests
use the replacement.

## 6. Transition and test the VMs

Start with a disposable canary using the same storage paths as production:

- Boot from an RBD-backed image or volume and verify disk writes.
- Attach a disposable volume and verify reads/writes.
- Live migrate to an updated compute host; verify storage access again.
- Exercise a controlled stop/start and volume reattachment as applicable.

Every running QEMU process loaded the old key at attach time and keeps it in
memory. A new secret in libvirtd only affects QEMU processes created after
the libvirt secret reload (step 5, item 5). Work through the compute group in this order, using the host just
completed as the migration target for the next one:

1. Confirm the libvirt secret reload succeeded on all four hosts, so any migration destination
   already holds the new secret.
2. openstack05: live migrate every VM off it to openstack04, openstack02, or
   openstack01. Confirm zero domains. If the step 1 image replacement for this
   host is still pending, run it now with `--limit openstack05`.
3. Live migrate VMs back onto openstack05 from openstack04, then repeat the
   drain for openstack04.
4. Repeat for openstack02, then openstack01. Any VM that cannot live migrate is
   stop/started through OpenStack so QEMU is recreated.
5. Track completion per VM. Include both RBD ephemeral disks and attached
   Cinder RBD volumes. A soft guest reboot does not recreate QEMU. See
   [OpenStack's guest transition requirement][ossn].

Each VM needs exactly one migration or stop/start after the libvirt secret reload; the host
order above only matters where the image replacement from step 1 is combined
with the credential transition. Finish inside the extended ticket window.

Do not treat continuing I/O from an old session as proof of successful migration.
Require fresh connections and successful storage operations with the new setup.

## 7. Complete the wider Ceph migration

OpenStack rotation addresses only some of the 27 legacy identities. The rest
are cephadm-managed daemon keys (osd, mgr, mds, rgw, crash, bootstrap) plus
`client.admin`. `ceph orch daemon rotate-key` is disabled in this Ceph version
(see [ceph PR 71805][pr71805]); do not use it. For each cephadm daemon listed by
`ceph orch ps`, rotate then redeploy:

```bash
sudo cephadm shell -- ceph auth rotate --key-type=aes256k <entity>
sudo cephadm shell -- ceph orch daemon redeploy <daemon-name>
```

Do one daemon type at a time, OSDs one at a time with `ceph status` clean
between each. Rotate `client.admin` last, with the new keyring placed on the
admin host through the secure path, and verify access before closing the
session that holds the old one. Confirm unused identities before removal;
names alone are not evidence that a credential is obsolete.

Once provisioning clients support the new cipher, choose it for new credentials
and prevent creation of legacy credentials:

```bash
sudo cephadm shell -- ceph mon set auth_preferred_cipher aes256k
sudo cephadm shell -- ceph config set mon mon_auth_allow_insecure_key false
sudo cephadm shell -- ceph config show mon.openstack01 mon_auth_allow_insecure_key
sudo cephadm shell -- ceph config show mon.openstack02 mon_auth_allow_insecure_key
sudo cephadm shell -- ceph config show mon.openstack04 mon_auth_allow_insecure_key
```

Run these on the Ceph administration host. Require `false` from all runtime
checks. During the investigation, `config get mon` reported `false` while the
running monitor reported `true`; checking only the stored/default value was
insufficient.

Wait for old rotating service keys to expire naturally. If their warning remains
after several hours, investigate before considering a forced wipe.

Before disabling legacy authentication, require all of the following:

- No `AUTH_INSECURE_CLIENT_KEY_TYPE` or `AUTH_INSECURE_SERVICE_KEY_TYPE` warning.
- All VM transitions and fresh-connection tests completed.
- Administrative access and all non-OpenStack consumers verified.

Only then:

```bash
sudo cephadm shell -- ceph mon set auth_allowed_ciphers aes256k
sudo cephadm shell -- ceph health detail
sudo cephadm shell -- ceph status
```

## Failure handling and completion evidence

- Before rotation: stop on an incompatible client, image-origin mismatch,
  failed canary, or incomplete guest transition plan. Retain legacy access.
- After rotation: prioritize securely delivering the new credential to failed
  consumers. Re-enabling `aes` or restoring an old local keyring does not restore
  the old value in Ceph's auth database.
- Do not roll consumers back to incompatible images after installing new keys.
- If access fails after cipher enforcement, use the previously tested recovery
  path and Ceph's documented emergency procedure; avoid improvised auth changes.
- Record image/package versions, Kolla command results, per-VM transition status,
  disposable storage test results, and final health output. Never attach keys,
  libvirt secret values, generic config dumps, or credential-bearing logs.
- Remove staging copies only after verification, following the established
  secret-retention procedure. Keep the authoritative deployment credentials.

Completion requires working fresh authentication, successful OpenStack storage
tests, and clearance of the four insecure-key warnings. Unrelated Ceph health
issues must be recorded separately.

## References

- [OpenStack security advisory OSSN-0108][ossn]
- [Ceph key migration procedure][ceph]
- [Kolla-Ansible 2026.1 external Ceph integration][external]
- [Kolla-Ansible 2026.1 operations][operations]
- [Kolla-Ansible stable/2026.1 Nova credential implementation][nova-tasks]
- [Ubuntu Ceph 19.2.6 package upload][ubuntu]
- [Ceph PR 71805: orch daemon rotate-key disabled, use auth rotate + redeploy][pr71805]
- [Kolla-Ansible stable/2026.1 nova-libvirt config.json (secrets copied at start)][libvirt-json]

[ossn]: https://docs.openstack.org/security-notes/OSSN-0108.html
[ceph]: https://docs.ceph.com/en/latest/rados/configuration/auth-config-ref/#upgrading-and-rotating-cephx-keys
[external]: https://docs.openstack.org/kolla-ansible/2026.1/reference/storage/external-ceph-guide.html
[operations]: https://docs.openstack.org/kolla-ansible/2026.1/user/operating-kolla.html
[nova-tasks]: https://github.com/openstack/kolla-ansible/blob/stable/2026.1/ansible/roles/nova-cell/tasks/external_ceph.yml
[ubuntu]: https://lists.ubuntu.com/archives/noble-changes/2026-August/058590.html
[pr71805]: https://github.com/ceph/ceph/pull/71805
[libvirt-json]: https://github.com/openstack/kolla-ansible/blob/stable/2026.1/ansible/roles/nova-cell/templates/nova-libvirt.json.j2

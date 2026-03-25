# Live Host Sync Plan

Date: 2026-03-25

## Goal

Reconcile this repo with the actual hosts listed in `host.yml`, using non-sensitive live host data as the source of truth. This review only used safe metadata: host identity, OS version, runtime network interfaces, hardware class, systemd unit presence for non-secret services, and container names for Kolla/cephadm.

## What The Live Hosts Actually Look Like

| Host | Live role signals | OS | Key runtime network facts | Notable hardware |
|---|---|---|---|---|
| `openstack01` | Kolla controller + compute containers, Ceph MON/MGR/RGW/OSD containers | Debian 13 | `bond0` slaves are `enp1s0f0np0` + `enp1s0f1np1` | ASPEED BMC VGA |
| `openstack02` | Kolla controller + compute containers, Ceph MON/MGR/RGW/OSD containers | Debian 13 | `bond0` slaves are `enp1s0f0np0` + `enp1s0f1np1` | ASPEED BMC VGA |
| `openstack04` | Kolla controller + compute containers, Ceph MON/RGW/OSD containers, `gpu-temp-monitor` active | Debian 13 | `bond0` slaves are `ens1f0np0` + `ens1f1np1` | 2x NVIDIA T10 |
| `openstack05` | Kolla compute containers, Ceph OSD containers | Debian 13 with backports kernel | runtime `bond0` slaves are `ens1f0np0` + `ens1f1np1`; on-disk bond config still says `enp3s0f0np0` + `enp3s0f1np1` | NVIDIA RTX A5000 + Intel Battlemage GPU |
| `openstack06` | Ceph OSD containers only | Debian 13 | `bond0` slaves are `enp179s0f0np0` + `enp179s0f1np1` | Matrox VGA |
| `arm01` | Temporary boot-only host, not part of the active steady-state fleet | Debian 12 | `bond0` slaves are `enp1s0f0np0` + `enp1s0f1np1` while powered on | ASPEED BMC VGA |

## Drift Found

### 1. Inventory NIC names are wrong for at least one host and ambiguous for another

- `host.yml` says `openstack04` uses `enp2s0f0np0` / `enp2s0f1np1`.
- The live host is actually bonded on `ens1f0np0` / `ens1f1np1`.
- `host.yml` says `openstack05` uses `enp3s0f0np0` / `enp3s0f1np1`.
- The live runtime bond uses `ens1f0np0` / `ens1f1np1`, but the on-disk `/etc/network/interfaces.d/bond0` still references `enp3...`.

Impact: rerunning the network template logic can render the wrong interface names and break the bond configuration.

### 2. `openstack06` is under-described in inventory

- It has a fully configured `bond0` network layout with the same VLAN scheme as the other x86 nodes.
- It participates in Ceph as an OSD host.
- `host.yml` does not declare `node_num`, `interface1`, `interface2`, or any storage-oriented grouping for it.

Impact: the repo cannot safely reproduce this node's current network/storage role.

### 3. `arm01` should not be modeled as an always-on production host

- It can boot and run compute-related containers, but it is only powered on for temporary use.
- The repo should distinguish it from the active x86 fleet so routine updates do not assume it is available.

Impact: treating `arm01` as steady-state inventory will create false failures and misleading topology.

### 4. The `cephadm` group name no longer matches reality

- The repo's `cephadm` group contains only `openstack01`.
- Live Ceph containers are present on `openstack01`, `openstack02`, `openstack04`, `openstack05`, and `openstack06`.

Impact: either the group is misnamed and is really "bootstrap node only", or the inventory is incomplete for current Ceph topology.

### 5. GPU automation should stay scoped to `openstack04`

- `gpu-monitor.yml` targets only `openstack04`, and the service is active there.
- `openstack05` has an NVIDIA RTX A5000 with its own fan, so it should not inherit the T10-specific thermal monitor from `openstack04`.
- vGPU is not intended to be enabled anywhere now.
- `setup/nvidia.yml` is stale and should be removed or archived:
  - it targets an undefined `nvidia-vgpu` group,
  - it references a missing `templates/vgpu_override.toml`,
  - it expects an external NVIDIA installer blob under `setup/files/`.

Impact: GPU automation should be simplified to the one real use case instead of preserving dead vGPU paths.

### 6. `openstack05` has special kernel and boot-parameter requirements that the repo does not model yet

- `openstack05` is the only host currently booted on a newer backports kernel: `6.19.6+deb13-amd64`.
- Its GRUB and live kernel command line include Battlemage-related flags:
  - `pci=realloc,big_root_window`
  - `xe.vram_bar_size=256`
- The live host also has Battlemage SR-IOV enabled with `sriov_numvfs=2` and `sriov_totalvfs=2` at `/sys/bus/pci/devices/0000:43:00.0/sriov_numvfs`.
- The repo has no playbook logic today to enable Debian backports or manage those host-specific boot flags.

Impact: a normal kernel update or reprovision from the current repo would miss the Battlemage kernel, boot flags, and SR-IOV setup on `openstack05`.

### 7. Secret-dependent bootstrap inputs are not documented well enough

- `bootstrap.yml` requires `templates/passwd.client.j2`, but that file is intentionally not tracked.
- This is correct from a security standpoint, but the repo does not explain how to supply it safely.

Impact: bootstrap is not reproducible from the repo alone, and operators have to know the secret file convention out of band.

### 8. Some public-repo exposure should be reduced even if it is not a credential leak

- `templates/authorized_keys` is tracked publicly and contains the exact SSH public keys granted host access.
- These are not private keys and do not force rotation by themselves, but they are still sensitive access metadata and should not be advertised in a public infra repo.
- VLAN IDs and subnet ranges are documented publicly, so those can stay if needed.
- Specific host IP assignments in `host.yml` are more sensitive and should be moved out of the public repo or templated from a private inventory source.

Impact: the repo does not appear to contain a tracked live credential, but it does expose access and topology details that are better kept private.

### 9. The fleet is not on one OS and kernel baseline

- The x86 nodes are on Debian 13.
- `openstack05` is on a Debian 13 backports kernel because of Battlemage support.
- `arm01` is still on Debian 12.

Impact: package names, kernel-module handling, and driver logic may need host- or group-specific conditionals.

### 10. GRUB boot parameters are already divergent across the fleet

- `openstack01` and `openstack02` boot with `intel_iommu=on iommu=pt` plus zswap settings.
- `openstack04` and `openstack05` boot with `amd_iommu=on iommu=pt` plus zswap settings.
- `openstack05` additionally needs `pci=realloc,big_root_window xe.vram_bar_size=256`.
- `openstack06` currently has zswap settings but no IOMMU flags.
- `arm01` currently boots with zswap settings and `quiet`.
- `openstack01` has a duplicate `GRUB_CMDLINE_LINUX_DEFAULT` line in `/etc/default/grub`.
- `openstack05` currently duplicates Battlemage flags in both `GRUB_CMDLINE_LINUX` and `GRUB_CMDLINE_LINUX_DEFAULT`.

Impact: kernel boot parameters should be modeled explicitly as host or group vars instead of being left to drift per machine.

Current observed GRUB state:

| Host | `GRUB_CMDLINE_LINUX` | `GRUB_CMDLINE_LINUX_DEFAULT` | Cleanup needed |
|---|---|---|---|
| `openstack01` | `intel_iommu=on iommu=pt zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.zpool=z3fold` | `console=tty0 console=ttyS0,115200 no_timer_check nofb nomodeset gfxpayload=text` | Remove duplicate `GRUB_CMDLINE_LINUX_DEFAULT` entry |
| `openstack02` | `intel_iommu=on iommu=pt zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.zpool=z3fold` | `console=tty0 console=ttyS0,115200 no_timer_check nofb nomodeset gfxpayload=text` | None |
| `openstack04` | `zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.zpool=z3fold amd_iommu=on iommu=pt` | `console=tty0 console=ttyS0,115200 no_timer_check nofb nomodeset gfxpayload=text` | None |
| `openstack05` | `zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.zpool=z3fold amd_iommu=on iommu=pt pci=realloc,big_root_window xe.vram_bar_size=256` | `console=tty0 console=ttyS0,115200 no_timer_check nofb gfxpayload=text pci=realloc,big_root_window xe.vram_bar_size=256` | Move Battlemage flags out of `GRUB_CMDLINE_LINUX_DEFAULT` |
| `openstack06` | `zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.zpool=z3fold` | `console=tty0 console=ttyS0,115200 no_timer_check nofb nomodeset gfxpayload=text` | None |
| `arm01` | `zswap.enabled=1 zswap.compressor=zstd zswap.max_pool_percent=25 zswap.zpool=z3fold` | `quiet` | Keep outside routine cleanup unless host becomes permanent |

## Desired vs Observed State

Before encoding live host observations as policy, each drift item must be classified. Some runtime state may be accidental drift that should be corrected, not recorded.

| Drift Item | Observed State | Desired State | Status |
|---|---|---|---|
| openstack04 NIC names | `ens1f0np0`/`ens1f1np1` | **Confirmed desired** — runtime bond is correct | RECORD |
| openstack05 NIC names | runtime `ens1f*`, on-disk `enp3*` | **Needs verification** — which naming rule should win? | VERIFY ON HOST |
| openstack05 duplicate GRUB flags | Battlemage flags in both CMDLINE vars | **Accident** — should only be in GRUB_CMDLINE_LINUX | CORRECT |
| openstack01 duplicate GRUB_CMDLINE_LINUX_DEFAULT | Two lines in /etc/default/grub | **Accident** — keep one line only | CORRECT |
| openstack06 no IOMMU flags | zswap only | **Needs decision** — does Ceph-only need IOMMU? | DECIDE |
| arm01 Debian 12 | Older OS than x86 fleet | **Accepted** — temporary host, not worth upgrading | ACCEPT |
| Ceph group = openstack01 only | Single host in cephadm group | **Bootstrap-only intent confirmed** — rename group | CORRECT |

## Update Plan

### Phase 1: Restructure the inventory model

Migrate from inlined host_vars in `host.yml` to Ansible best-practice `host_vars/` directory structure. This prevents the exact kind of variable drift that caused the current problems.

```
BEFORE                          AFTER
══════════                      ══════════
host.yml:                       host.yml:
  [all]                           [all]
  openstack01 node_num=1 ...      openstack01
  openstack02 node_num=2 ...      openstack02
  ...                             ...
                                  [ceph_bootstrap]
                                  openstack01

                                host_vars/
                                  openstack01.yml
                                  openstack02.yml
                                  openstack04.yml
                                  openstack05.yml
                                  openstack06.yml
                                  arm01.yml
```

1. Create `host_vars/` directory with one file per host containing:
   - `node_num`, `interface1`, `interface2` (per-host network facts)
   - `grub_cmdline_linux` and `grub_cmdline_linux_default` (per-host GRUB flags, see Phase 3)
   - Any host-specific variables (backports kernel for openstack05, etc.)
2. Correct `openstack04` interface names to `ens1f0np0` / `ens1f1np1`.
3. Resolve `openstack05` interface naming with one final validation step before editing:
   - prefer runtime bond slave names over stale config file contents,
   - but verify whether persistent naming rules intentionally expose `enp3...` during boot.
4. Add `node_num=6`, `interface1=enp179s0f0np0`, and `interface2=enp179s0f1np1` for `openstack06`.
5. Simplify `host.yml` to group membership only:
   - Rename `cephadm` to `ceph_bootstrap` (only one consumer exists — do NOT create speculative `ceph_mon`/`ceph_osd`/`ceph_rgw` groups until real playbooks need them).
   - Keep `arm01` out of steady-state rollout groups and mark it as temporary or disabled-by-default.
   - Model `openstack06` explicitly as a Ceph-only host via group membership.
6. Fix `group_vars/all.yml`: change `net.core.default_qdisc: fq_codel` to `fq` to match what the BBR role actually requires.

### Phase 2: Split bootstrap.yml into focused roles

The current `bootstrap.yml` is a 230-line monolith mixing unrelated concerns. Split into focused, testable roles:

```
roles/
  base/        <-- SSH keys, packages, NTP/chrony, cpupower, unattended-upgrades
  network/     <-- bond0.j2 deploy to ALL hosts (x86 + ARM), resolved.conf
  mail/        <-- exim4 config, aliases, mailname, root redirect
  kvm/         <-- nested virtualization (Intel/AMD/ARM-aware)
  grub/        <-- template-based GRUB management (Phase 3)
  ceph-bootstrap/  <-- cephadm download + install (fix el9 RPM URL)
  bbr/         <-- existing submodule (no changes)
```

`bootstrap.yml` becomes a thin orchestrator that includes these roles with appropriate host/group targeting.

Specific changes:
1. **Network role** deploys `bond0.j2` to ALL hosts (not just ARM). This closes the gap where x86 hosts had bond configured out-of-band.
2. **KVM role** detects CPU vendor via `ansible_processor` / `ansible_architecture`:
   - Intel hosts: `options kvm_intel nested=1`
   - AMD hosts: `options kvm_amd nested=1`
   - ARM hosts (Ampere): skip deployment entirely (ARM KVM is built into the kernel).
3. **Ceph-bootstrap role** fixes the cephadm download URL from the el9 RPM path (`rpm-19.2.0/el9/noarch/cephadm`) to the correct Debian/generic path.
4. **Remove `setup/roles/bbr`** — it duplicates the existing `roles/bbr` submodule with a stale `remote_user: sysops`.
5. **Remove `setup/nvidia.yml`** — stale vGPU automation targeting undefined `nvidia-vgpu` group.
6. Keep `gpu-monitor.yml` scoped to `openstack04` only. Add a comment explaining the T10-specific thermal monitoring should not be expanded to `openstack05`.
7. **Fold `pci-pass.yml` VFIO/blacklist module loading into the GRUB role** (IOMMU flags move to host_vars, VFIO module loading becomes part of the role). Retire `pci-pass.yml`'s GRUB manipulation logic.
8. **Scope `exporter.yml`** to exclude `arm01` or gate on `bond0.1113` existence check.

### Phase 3: Template-based GRUB management

Replace the fragile `lineinfile` regex-append pattern (currently in `swap.yml`, `pci-pass.yml`, and `setup/nvidia.yml`) with a single GRUB role that templates the entire `/etc/default/grub` from host_vars.

```
host_vars/openstack01.yml:
  grub_cmdline_linux:
    - intel_iommu=on
    - iommu=pt
    - zswap.enabled=1
    - zswap.compressor=zstd
    - zswap.max_pool_percent=25
    - zswap.zpool=z3fold
  grub_cmdline_linux_default:
    - console=tty0
    - console=ttyS0,115200
    - no_timer_check
    - nofb
    - nomodeset
    - gfxpayload=text
```

The role:
1. Templates the entire `/etc/default/grub` file (idempotent, no ordering issues).
2. Joins the list into a space-separated string for each GRUB variable.
3. Includes a handler to run `update-grub2` only when the file changes.
4. Includes VFIO module loading and nouveau blacklist for hosts that need GPU passthrough.

Host-specific GRUB state (desired, not just observed):
- `openstack01`, `openstack02`: `intel_iommu=on iommu=pt` + zswap
- `openstack04`, `openstack05`: `amd_iommu=on iommu=pt` + zswap
- `openstack05`: additionally `pci=realloc,big_root_window xe.vram_bar_size=256` (in CMDLINE_LINUX only, NOT in CMDLINE_LINUX_DEFAULT)
- `openstack06`: zswap only (IOMMU status to be decided)
- `arm01`: keep outside routine GRUB management unless host becomes permanent

After the GRUB role is in place, **retire GRUB manipulation from `swap.yml` and `pci-pass.yml`** to avoid two control planes editing `/etc/default/grub`.

### Phase 3b: openstack05 Battlemage requirements

Keep in the same PR as Phase 3 since the GRUB role naturally handles openstack05's flags.

1. Enable the required Debian backports source on openstack05.
2. Pin or install the newer kernel from backports.
3. GRUB parameters handled by the GRUB role (Phase 3).
4. Add an idempotent `systemd` oneshot to enforce `echo 2 > /sys/bus/pci/devices/0000:43:00.0/sriov_numvfs` after reboot.
5. Keep this logic host-scoped via host_vars so it does not leak to the rest of the fleet.

### Phase 4: Make secret handling explicit without committing secrets

1. Add operator documentation for secret-backed files.
   - `templates/passwd.client.j2` should remain out of git.
   - Document where it must be created and which playbooks depend on it.
2. Prefer an example or README note over checking in any credential-bearing template.

### Phase 5: Reduce public exposure of access and inventory details

1. Remove `templates/authorized_keys` from the public repo and source it from a private path, private submodule, or operator-supplied file at apply time.
2. Do not force SSH public-key rotation just because the public keys were published.
   - Treat this as exposure reduction, not credential invalidation.
3. With the host_vars/ migration (Phase 1), sensitive per-host data (IP assignments) is naturally isolated into files that can be gitignored or sourced from a private overlay.
4. If the repo stays public, split inventory into:
   - public topology-safe data (group membership, VLAN IDs, subnet scheme),
   - private host addressing and access-control data (host_vars with IPs, authorized_keys).

### Phase 5b: Additional cleanup

1. Check live hosts for `/extraswap` file (swap.yml bug creates 8GB dead file on every host). If unused, remove the `dd` command from swap.yml and clean up `/extraswap` on hosts.
2. Check live hosts for `ens1np0.j2` usage. If no host uses a single-NIC (non-bonded) VLAN layout, remove the orphaned template.

### Phase 6: Add testing foundation

Before applying any changes to live hosts, establish automated validation:

1. Add `ansible-lint` and `yamllint` config files for static analysis.
2. Write a `scripts/validate.sh` that runs:
   - `yamllint` on all YAML files
   - `ansible-lint` on all playbooks
   - Inventory validation: check every `hosts:` target in all playbooks maps to a non-empty group
   - Template rendering tests: render `bond0.j2` and GRUB template for each host and verify output
3. Add GRUB role idempotency test: run the role twice, verify no changes on second run.

### Phase 7: Validate before applying to hosts

1. Run `scripts/validate.sh` after all code changes.
2. Render the `bond0` template per host and compare it to the current live runtime addresses and bond slaves.
3. Verify the GRUB parameter model against the current host configuration before rollout.
4. Verify the `openstack05` SR-IOV unit restores `sriov_numvfs=2` after reboot.
5. Dry-run bootstrap tasks with secret files supplied out-of-band.
6. Confirm the updated group layout matches the live container placement:
   - controller services on `openstack01`, `openstack02`, `openstack04`,
   - compute services on `openstack01`, `openstack02`, `openstack04`, `openstack05`,
   - Ceph OSD presence on `openstack01`, `openstack02`, `openstack04`, `openstack05`, `openstack06`.
7. Validate the `openstack05` backports kernel path separately from the other hosts.

## Recommended Change Order

1. Migrate inventory to `host_vars/` and fix group membership (Phase 1).
2. Split `bootstrap.yml` into focused roles (Phase 2).
3. Implement GRUB role and retire old GRUB mutators (Phase 3).
4. Add openstack05 Battlemage support (Phase 3b).
5. Remove stale automation: `setup/nvidia.yml`, `setup/roles/bbr`, retire `pci-pass.yml` GRUB logic (Phase 2).
6. Add testing foundation and validation script (Phase 6).
7. Reduce public exposure and document secret handling (Phases 4-5).
8. Run validation and live host checks (Phase 5b, Phase 7).

## Open Questions To Resolve During Implementation

1. ~~Should `cephadm` mean "bootstrap node" or "all Ceph-managed hosts"?~~ **RESOLVED: Rename to `ceph_bootstrap`. Add more Ceph groups only when real playbooks need them.**
2. For `openstack05`, should the repo follow runtime slave names (`ens1f*`) or preserve the current boot-time config naming (`enp3*`) after one more host-side validation?
3. Should `arm01` stay in the inventory as a documented temporary host, or be moved to a separate inventory file that is only loaded when needed?
4. Does `openstack06` (Ceph-only) need IOMMU flags? (Currently has zswap only.)
5. Is `ens1np0.j2` used by any host? (Check live hosts before removing.)
6. Is `/extraswap` mounted or active on any host? (Check live hosts before fixing swap.yml.)

## Success Criteria

- Per-host facts live in `host_vars/` directory, not inlined in `host.yml`.
- `host.yml` contains group membership only, with correct topology.
- All runnable playbooks target real groups that exist in inventory.
- GRUB parameters are managed by a single idempotent role, not fragmented across playbooks.
- `bootstrap.yml` is split into focused, testable roles.
- Bond template deploys to all hosts (x86 + ARM), not just ARM.
- KVM nested config detects CPU vendor (Intel/AMD) and skips ARM.
- Broken references are removed or repaired (`setup/nvidia.yml`, `setup/roles/bbr`, stale GRUB mutators).
- `scripts/validate.sh` passes: yamllint, ansible-lint, inventory validation, template rendering.
- Public repo contents no longer expose exact SSH access keys or specific host IP assignments.
- Secret-backed inputs stay out of git but are clearly documented.
- A dry-run does not propose obviously incorrect network or GPU changes for any host.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 1 | issues_found | 5 findings from outside voice, all resolved |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 15 issues, 2 critical gaps (testing covers both) |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | N/A | No UI scope — infrastructure plan |

- **CODEX:** Found 5 structural gaps: canonizing-accidents risk, speculative Ceph groups, inventory model fragility, cephadm el9 URL bug, openstack05 blast radius. All resolved via AskUserQuestion.
- **CROSS-MODEL:** 5 tension points between Claude review and Codex outside voice. Agreement reached on all 5 after user input.
- **UNRESOLVED:** 0 decisions unresolved.
- **VERDICT:** ENG CLEARED — ready to implement. Run `/ship` when done.

# Ceph Config Role

[中文](README.md)

This role manages Ceph day-2 configuration through an audit-apply-verify workflow for cephadm-managed clusters. All settings are centralized in `group_vars/ceph_cluster.yml` as structured data, supporting both present (set) and absent (remove) operations.

## Usage

### Audit

Review current Ceph configuration drift against the desired state (read-only, makes no changes):

```bash
cd ansible
ansible-playbook playbooks/ceph-audit.yml --limit ceph_bootstrap
```

### Apply

Apply configuration changes. Requires explicit `-e ceph_iac_apply=true` to execute changes:

```bash
cd ansible
ansible-playbook playbooks/ceph-apply.yml --limit ceph_bootstrap -e ceph_iac_apply=true
```

A pre-apply config snapshot is automatically created in `/var/lib/ceph-iac/snapshots/`.

### Verify

Confirm live cluster state matches the desired configuration:

```bash
cd ansible
ansible-playbook playbooks/ceph-verify.yml --limit ceph_bootstrap
```

## Managed Scope

- **config entries**: `ceph config` settings from the `ceph_config_desired` list (network, mon, mgr, OSD memory, BlueStore, scrub, heartbeat, RGW Keystone)
- **cephadm labels**: automatically derived from Ansible inventory groups (`cephadm_label_map`)
- **service specs**: MON, MGR, RGW specs under `ansible/files/cephadm/specs/`

## Not in Scope

- OSD drivegroup specs
- Pool configuration
- CephFS/MDS
- Dashboard configuration
- Ceph bootstrap lifecycle

## Configuration Files

- `group_vars/ceph_cluster.yml`: configuration baseline (desired + audit-only + ignore patterns + label map)
- `roles/ceph-config/defaults/main.yml`: role defaults (apply gate, snapshot paths)
- `files/cephadm/specs/*.yml`: cephadm service spec files

## File Structure

```
ansible/roles/ceph-config/
  defaults/main.yml      # ceph_iac_apply, snapshot/staging paths
  tasks/
    main.yml             # full audit-apply-verify cycle
    audit.yml            # config dump + drift detection
    apply.yml            # snapshot + set/rm config entries
    verify.yml           # assert live state matches desired
    labels.yml           # inventory-derived label reconciliation
    specs.yml            # cephadm service spec application
```

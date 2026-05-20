# Neutron Patch And Custom Kolla Image Guide

## Goal

Carry a downstream Neutron fix for the OVN router gateway LRP drift / floating IP crash path, build custom Kolla images, and deploy them safely on this `2025.2` environment.

This guide assumes:

- OpenStack release: `2025.2`
- Kolla-Ansible config in this repo under `kolla/`
- image registry from this repo:
  - `docker_registry: registry.cloudnative.tw`
  - `docker_namespace: kolla`

## What To Patch

### Patch 1: harden floating-IP programming

In `neutron/plugins/ml2/drivers/ovn/mech_driver/ovsdb/ovn_client.py`, guard the gateway LRP lookup in `_create_or_update_floatingip`.

Current upstream behavior on both `master` and `stable/2025.2` is effectively:

```python
gw_port_id = router_db.get('gw_port_id')
lrp = self._nb_idl.get_lrouter_port(gw_port_id)
if lrp.options.get(...):
    columns['gateway_port'] = lrp.uuid
```

This crashes if `lrp` is missing.

Recommended downstream behavior:

```python
gw_port_id = router_db.get('gw_port_id')
lrp = self._nb_idl.get_lrouter_port(gw_port_id) if gw_port_id else None
if not lrp:
    LOG.warning(
        "Gateway LRP for router %s and gw_port %s is missing; "
        "skipping gateway_port on floating IP %s",
        router_id, gw_port_id, floatingip['id'])
elif lrp.options.get(ovn_const.LRP_OPTIONS_RESIDE_REDIR_CH) == 'true':
    columns['gateway_port'] = lrp.uuid
```

That prevents the crash and allows the rest of the floating-IP path to proceed.

### Patch 2: harden router-port repair

In the OVN maintenance and router-port update flow, treat a missing `Logical_Router_Port` as a recreate condition, not an unhandled failure.

Target areas:

- `maintenance.py`
- `ovn_client.py` router-port update helpers

Desired behavior:

- if `TYPE_ROUTER_PORTS` is inconsistent and `get_lrouter_port()` returns `None`, call the create path for that router gateway port
- do not fail maintenance simply because the LRP disappeared between detection and repair

### Patch 3: add regression tests

Add tests for:

- floating-IP association when `gw_port_id` exists in Neutron but `lrp-<gw_port_id>` is missing in OVN
- maintenance recovery when a router gateway LRP disappears after router creation

## Upstream Status

As of 2026-04-12:

- `stable/2025.2`: still vulnerable for this exact floating-IP path
- `master`: still contains the same unsafe dereference in the floating-IP code path
- `master` has extra guards in some maintenance code paths, but not enough to eliminate this issue class

Primary sources:

- Kolla-Ansible 2025.2 operating notes:
  https://docs.openstack.org/kolla-ansible/2025.2/user/operating-kolla.html
- Kolla image-building docs:
  https://docs.openstack.org/kolla/latest/admin/image-building.html
- Neutron `master` OVN client:
  https://opendev.org/openstack/neutron/raw/branch/master/neutron/plugins/ml2/drivers/ovn/mech_driver/ovsdb/ovn_client.py
- Neutron `stable/2025.2` OVN client:
  https://opendev.org/openstack/neutron/raw/branch/stable/2025.2/neutron/plugins/ml2/drivers/ovn/mech_driver/ovsdb/ovn_client.py

## Build Strategy

Use a patched local Neutron source checkout for `neutron-base` and rebuild only the Neutron images.

This is cleaner than editing containers in place and easier to rebuild than patching installed files after image creation.

## Build Host Prerequisites

On the image build host:

```bash
python3 -m pip install --upgrade git+https://opendev.org/openstack/kolla@stable/2025.2
python3 -m pip install --upgrade git+https://opendev.org/openstack/kolla-ansible@stable/2025.2
python3 -m pip install docker
```

Log in to the registry if needed:

```bash
docker login registry.cloudnative.tw
```

## Prepare Source Trees

Create a working directory:

```bash
mkdir -p ~/src/openstack/2025.2
cd ~/src/openstack/2025.2
```

Clone Neutron and create a downstream branch:

```bash
git clone https://opendev.org/openstack/neutron
cd neutron
git checkout stable/2025.2
git switch -c downstream/fip-gw-lrp-fix
```

Apply your downstream patch and commit it with a clear message.

## Create `kolla-build.conf`

Create a local build config, for example at `~/src/openstack/2025.2/kolla-build.conf`:

```ini
[DEFAULT]
base = ubuntu
namespace = kolla
registry = registry.cloudnative.tw
push = true
tag = 2025.2-neutron-fipfix1
locals_base = /home/debian/src/openstack/2025.2

[neutron-base]
type = local
location = $locals_base/neutron
```

Notes:

- `namespace` and `registry` keep the image names aligned with this repo.
- `tag` gives you a distinct deployable image version.
- `locals_base` and `type = local` follow the Kolla-supported local source override flow.

## Build The Custom Images

Build only the Neutron image family:

```bash
kolla-build \
  --config-file ~/src/openstack/2025.2/kolla-build.conf \
  ^neutron-
```

That will build and push images such as:

- `registry.cloudnative.tw/kolla/neutron-server:2025.2-neutron-fipfix1`
- `registry.cloudnative.tw/kolla/neutron-rpc-server:2025.2-neutron-fipfix1`
- `registry.cloudnative.tw/kolla/neutron-ovn-agent:2025.2-neutron-fipfix1`
- `registry.cloudnative.tw/kolla/neutron-ovn-maintenance-worker:2025.2-neutron-fipfix1`

## Wire The New Tag Into This Repo

This repo already derives `openstack_tag` from:

- `openstack_release`
- `openstack_tag_suffix`

Current definition in [kolla/globals.yml](/Users/igene/Documents/cntug/infra-labs/infra-labs-ops/kolla/globals.yml:41):

```yaml
openstack_tag: "{{ openstack_release ~ openstack_tag_suffix }}"
```

To deploy the custom images, set:

```yaml
openstack_tag_suffix: "-neutron-fipfix1"
```

That makes the deployed tag:

- `2025.2-neutron-fipfix1`

## Deploy The Patched Images

Inside a series, Kolla-Ansible’s documented pattern is:

- rebuild images if needed
- pull images
- run `kolla-ansible deploy` again

For this repo:

```bash
kolla-ansible -i kolla/multinode --configdir kolla pull
kolla-ansible -i kolla/multinode --configdir kolla deploy --tags neutron
```

Recommended rollout order:

1. validate in a non-production environment first
2. run `prechecks`
3. pull the new images
4. deploy with `--tags neutron`
5. run the drift checker and floating-IP smoke tests

## Smoke Test After Deployment

Run:

```bash
./admin_scripts/check_ovn_gateway_lrp_drift.sh --nb-db-host 192.168.0.21
```

Then validate a controlled FIP workflow:

1. create or pick a test VM
2. associate a floating IP
3. confirm the FIP becomes `ACTIVE`
4. confirm OVN now has the `dnat_and_snat` row
5. disassociate and reassociate once more

## Rollback

Rollback is just a tag rollback:

1. revert `openstack_tag_suffix`
2. pull the previous image tag
3. redeploy Neutron

Example:

```bash
git checkout -- kolla/globals.yml
kolla-ansible -i kolla/multinode --configdir kolla pull
kolla-ansible -i kolla/multinode --configdir kolla deploy --tags neutron
```

If you do not want to overwrite the default tag in the registry, keep old and new tags side by side and switch only `openstack_tag_suffix`.

## Recommendation

I would carry this as a downstream patch until upstream lands a proper fix for both:

- the floating-IP crash path
- the router gateway LRP self-healing path

That gives you:

- immediate protection against the Neutron exception
- reproducible images
- a clean rollback path
- no manual edits inside live containers

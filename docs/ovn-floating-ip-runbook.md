# OVN Floating IP Drift Runbook

## Purpose

This runbook covers the intermittent failure mode where:

- a floating IP is associated in Neutron
- the floating IP remains `DOWN`
- OVN is missing the `dnat_and_snat` NAT entry
- the router external gateway `lrp-<gw_port_id>` is missing or inconsistent

For the RCA and detailed timeline, see:

- [reports/floating-ip-ovn-rca-2026-04-12.md](/Users/igene/Documents/cntug/infra-labs/infra-labs-ops/reports/floating-ip-ovn-rca-2026-04-12.md:1)

## Symptoms

- `openstack floating ip show <id>` shows `status: DOWN` even though `port_id` is set
- `neutron-server` logs `AttributeError: 'NoneType' object has no attribute 'options'`
- `neutron-ovn-maintenance-worker` logs `Failed to fix resource ... type: router_ports`
- OVN northbound lacks:
  - `lrp-<gw_port_id>` for the router external gateway
  - the expected `dnat_and_snat` row for the floating IP

## Read-Only Health Check

Use the script in this repo:

```bash
./admin_scripts/check_ovn_gateway_lrp_drift.sh --nb-db-host 192.168.0.21
```

To scope the check to one router:

```bash
./admin_scripts/check_ovn_gateway_lrp_drift.sh \
  --nb-db-host 192.168.0.21 \
  --router-id 55128602-9fa5-4ed9-886d-96847c6ac2d6
```

What it verifies:

- each router external gateway port has a matching OVN logical router port
- each associated floating IP has a matching OVN `dnat_and_snat` NAT row

Exit codes:

- `0`: no issues found
- `2`: drift detected

## Immediate Recovery

### Lowest-risk operator recovery

Rebuild the router external gateway through Neutron:

1. Record the current external gateway network and fixed IPs.
2. Remove the external gateway from the affected router.
3. Re-add the same external gateway.
4. Re-test or re-associate the floating IP if required.

This is disruptive for north-south traffic on the affected router.

Example:

```bash
openstack router show 55128602-9fa5-4ed9-886d-96847c6ac2d6 -f yaml

openstack router unset --external-gateway 55128602-9fa5-4ed9-886d-96847c6ac2d6

openstack router set \
  --external-gateway public \
  55128602-9fa5-4ed9-886d-96847c6ac2d6
```

If the floating IP still has a stale association, disassociate and reassociate it:

```bash
openstack floating ip unset --port 106f72ce-b030-4b15-95e4-933c62691d55
openstack floating ip set --port ff3762a7-9542-4aa5-9a43-b208177b1771 106f72ce-b030-4b15-95e4-933c62691d55
```

## What Not To Do

- Do not hand-edit OVN northbound rows unless you are prepared to own reconciliation fallout.
- Do not broad-read live Kolla or OpenStack secret-bearing config files just to debug this.
- Do not assume `br-ex` or security groups are at fault before checking the router gateway LRP.

## Recommended Monitoring

Alert on:

- associated floating IPs that remain `DOWN`
- `AttributeError: 'NoneType' object has no attribute 'options'` in `neutron-server`
- `Failed to fix resource ... type: router_ports` in `neutron-ovn-maintenance-worker`

Suggested periodic job:

```bash
./admin_scripts/check_ovn_gateway_lrp_drift.sh --nb-db-host 192.168.0.21
```

Run it from the deploy host or another host that has:

- `openstack` CLI with admin credentials
- SSH access to a controller hosting `ovn_nb_db`

## Longer-Term Fix

This needs a Neutron-side fix, not just an operational workaround.

See:

- [docs/neutron-kolla-custom-image-guide.md](/Users/igene/Documents/cntug/infra-labs/infra-labs-ops/docs/neutron-kolla-custom-image-guide.md:1)

That guide covers:

- a downstream patch plan
- how to build custom Neutron Kolla images
- how to deploy and roll back them in this environment

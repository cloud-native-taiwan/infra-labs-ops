# ADR-0008: Neutron OVN distributed FIPs with a patched Kolla image

- **Status:** Accepted
- **Date:** 2026-04-12
- **Deciders:** CNTUG ops

## Context

Distributed floating IPs (`neutron_ovn_distributed_fip`) spread floating-IP
dataplane work across compute nodes instead of funnelling it through a gateway
chassis. A separate defect blocks this in practice: OVN gateway LRP drift causes
floating-IP creation to crash. A missing logical-router-port dereference yields
an AttributeError on a NoneType `options`, and the maintenance worker cannot
self-heal the affected router gateway ports. The defect affects both 2025.2 and
master.

## Decision

1. Enable `neutron_ovn_distributed_fip: yes`.
2. Carry a downstream Neutron patch that guards the LRP dereference (treating a
   missing port as a non-fatal warning) and hardens router-port repair. Build
   custom Kolla images locally from the patched source (`type=local`), tag them
   (for example `2025.2-neutron-fipfix1`), push to the registry, and deploy via
   `openstack_tag_suffix`. Rollback is reverting the tag and redeploying.
3. Ship a read-only drift checker (`check_ovn_gateway_lrp_drift.sh`) and a
   documented recovery: unset, then re-set the router external gateway.

## Alternatives considered

- Hand-edit installed files inside the running containers. Rejected: not
  reproducible and gives no clean rollback.
- Hand-edit the OVN northbound DB. Rejected: risky reconciliation fallout.
- Wait for the upstream fix. Rejected: the defect affects production now.

## Consequences

Distributed FIPs improve dataplane scale. The custom image is a maintenance
burden until the fix merges upstream, but tag-based rollback keeps it safe.
Drift is detectable via the read-only script and alerts (FIP DOWN,
neutron-server AttributeError).

## References

- docs/neutron-kolla-custom-image-guide.md
- docs/ovn-floating-ip-runbook.md
- kolla/globals.yml ~668 (neutron_ovn_distributed_fip)

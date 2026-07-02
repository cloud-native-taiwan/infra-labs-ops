# ADR-0007: Octavia OVN-only provider (drop amphora)

- **Status:** Accepted
- **Date:** 2026-05-01
- **Deciders:** CNTUG ops

## Context

Octavia traditionally ships with the amphora provider, which runs a lightweight
load-balancer VM per tenant LB. Amphora carries ongoing operational overhead:
image lifecycle management and certificate management for the control-to-amphora
channel. The lab only ever uses the OVN provider, which steers traffic over OVN
virtual networks directly and needs none of that machinery.

## Decision

Pin Octavia to OVN only:

- `octavia_provider_drivers: "ovn:OVN provider"`
- `octavia_provider_agents: "ovn"`
- `octavia_auto_configure: no`
- Remove the `octavia_certs_*` variables.

This gates the amphora-specific tasks inside kolla-ansible and stops the API
from advertising a provider the lab does not support.

## Alternatives considered

- Keep amphora available alongside OVN. Rejected: it is unused and pulls in
  certificate tasks and VM lifecycle that nobody operates.
- Leave `octavia_auto_configure` on. Rejected: the OVN provider uses virtual
  networks directly and does not need the auto-configuration flow.

## Consequences

No amphora image or certificate management to maintain; load balancers ride OVN
virtual networks. If amphora-only features are ever required, this decision must
be revisited and the cert configuration restored.

## References

- commit dadbe2a (pin OVN-only, drop amphora cert config)
- kolla/globals.yml ~854-859

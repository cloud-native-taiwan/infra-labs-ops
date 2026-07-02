# ADR-0006: RabbitMQ messaging topology and partition handling

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context
RabbitMQ was migrated to quorum queues for durability, but transient oslo.messaging RPC reply queues are high-churn (created and destroyed per call), and quorum backing on them matched an observed oslo.messaging timeout path. Separately, a known broker bug (Bug #2126768) affects clustering behavior.

## Decision
Split queue durability by purpose and pin upgrade policy in-repo.

- Keep durable service queues as quorum (`om_enable_rabbitmq_quorum_queues: true`).
- Render transient reply queues as classic (`om_enable_rabbitmq_transient_quorum_queue: false`), overriding it in service configs.
- Keep stream fanout disabled (`om_enable_rabbitmq_stream_fanout: false`) because after a Nova restart it loses the fanout connection (no local replica), causing brief outages.
- Run with `partition_handling=ignore` (deliberate, Bug #2126768 workaround) — nodes do not self-pause on the minority side.
- Pin `rabbitmq_allowed_upgrades` to upstream 2026.1 defaults so the version-jump policy is versioned in-repo.

## Alternatives considered
- Quorum for everything including transient replies — rejected; timeout path, and queue type is fixed at creation.
- Enable stream_fanout — rejected; Nova restart outage.
- `pause_minority` partition handling — not chosen due to Bug #2126768.

## Consequences
Durable queues survive node loss while transient replies stay cheap. `partition_handling=ignore` means a split cannot be detected from a single node's view — the health-gate and control-plane collector must query EVERY node to detect partitions (see ADR-0017, ADR-0023).

## References
- commits a900690, 55804b4, 978632a (host tuning + quorum migration)
- commit 534b27b (pin allowed upgrades)
- kolla/globals.yml ~109-114, 469-479
- RabbitMQ workaround note (Bug #2126768)

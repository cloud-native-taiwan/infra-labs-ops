# ADR-0006: RabbitMQ messaging topology and partition handling

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context
RabbitMQ was migrated to quorum queues for durability, but transient oslo.messaging RPC reply queues are high-churn (created and destroyed per call), and quorum backing on them matched an observed oslo.messaging timeout path. Separately, a known broker bug (Bug #2126768) affects clustering behavior.

## Decision
Split queue durability by purpose and pin upgrade policy in-repo.

- Keep durable service queues as quorum. Since kolla-ansible 2025.1 this is not optional: every service template hard-codes `rabbit_quorum_queue = true`, and the old `om_enable_rabbitmq_quorum_queues` toggle is no longer read (removed from `kolla/globals.yml` 2026-11).
- Render transient reply queues as classic with `rabbit_transient_quorum_queue = false` in `kolla/config/global.conf`, which kolla merges over every service config. Kolla templates hard-code `true`, and the old `om_enable_rabbitmq_transient_quorum_queue` toggle has had no effect since 2025.1 (kolla-ansible commit 50bbcb09d), so `global.conf` is the only thing that keeps reply queues classic. RabbitMQ 4.3 denies classic transient non-exclusive queues by default, so `globals.yml` permits them with `rabbitmq_extra_config: deprecated_features.permit.transient_nonexcl_queues`.
- Disable the oslo.messaging queue manager (`use_queue_manager = false` in `kolla/config/global.conf`). With it on, nova-conductor workers ended up sharing reply queue names (2-4 consumers per `reply_<host>:nova-conductor:N`), so replies went to the wrong worker and `select_destinations` timed out (2026-10-26, oslo.messaging bug #2110957). Reply queues get per-process random names again.
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
- kolla/globals.yml (Messaging options, `rabbitmq_allowed_upgrades`, `rabbitmq_extra_config`); kolla/config/global.conf
- kolla-ansible commit 50bbcb09d "Don't allow quorum queues to be disabled": https://opendev.org/openstack/kolla-ansible/commit/50bbcb09d0d7cbff5858a9b8b416838ef25d1d1f
- RabbitMQ workaround note (Bug #2126768)

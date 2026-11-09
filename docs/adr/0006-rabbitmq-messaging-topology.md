# ADR-0006: RabbitMQ messaging topology and partition handling

- **Status:** Accepted
- **Date:** 2026-04-01
- **Deciders:** CNTUG ops

## Context
RabbitMQ was migrated to quorum queues for durability, but transient oslo.messaging RPC reply queues are high-churn (created and destroyed per call), and quorum backing on them matched an observed oslo.messaging timeout path. Separately, oslo.messaging Launchpad bug 2126768 (a stream fanout queue missing its local replica makes clients fail in a loop) affects stream fanout.

## Decision
Split queue durability by purpose and pin upgrade policy in-repo.

- Keep durable service queues as quorum. Since kolla-ansible 2025.1 this is not optional: every service template hard-codes `rabbit_quorum_queue = true`, and the old `om_enable_rabbitmq_quorum_queues` toggle is no longer read (removed from `kolla/globals.yml` 2026-11).
- Render transient reply queues as classic with `rabbit_transient_quorum_queue = false` in `kolla/config/global.conf`, which kolla merges over every service config. Kolla templates hard-code `true`, and the old `om_enable_rabbitmq_transient_quorum_queue` toggle has had no effect since 2025.1 (kolla-ansible commit 50bbcb09d), so `global.conf` is the only thing that keeps reply queues classic. RabbitMQ 4.3 denies classic transient non-exclusive queues by default, so `globals.yml` permits them with `rabbitmq_extra_config: deprecated_features.permit.transient_nonexcl_queues`.
- Disable the oslo.messaging queue manager (`use_queue_manager = false` in `kolla/config/global.conf`). With it on, nova-conductor workers ended up sharing reply queue names (2-4 consumers per `reply_<host>:nova-conductor:N`), so replies went to the wrong worker and `select_destinations` timed out (2026-10-26, oslo.messaging bug #2110957). Reply queues get per-process random names again.
- Keep stream fanout disabled (`om_enable_rabbitmq_stream_fanout: false`, Launchpad bug 2126768) because after a Nova restart it loses the fanout connection (no local replica), causing brief outages.
- Leave partition handling at the kolla default. Correction (2026-11): an earlier revision said this fleet runs `partition_handling=ignore` as a Bug #2126768 workaround. It was wrong: git history shows nothing in this repo ever overrode `rabbitmq_cluster_partition_handling`, so kolla-ansible 2026.2 renders `cluster_partition_handling = pause_minority`, and bug 2126768 is about stream fanout, not partitions. On RabbitMQ 4.3, which kolla 2026.2 ships, the key is accepted but has no effect: 4.3 is Khepri (Raft) only and dropped the Mnesia-era partition handling strategies. Raft majority rules now decide which side keeps serving.
- Pin `rabbitmq_allowed_upgrades` to upstream 2026.1 defaults so the version-jump policy is versioned in-repo.

## Alternatives considered
- Quorum for everything including transient replies — rejected; timeout path, and queue type is fixed at creation.
- Enable stream_fanout — rejected; Nova restart outage.
- Override partition handling (`ignore` / `autoheal`) — moot on RabbitMQ 4.3, where the setting is ignored.

## Consequences
Durable queues survive node loss while transient replies stay cheap. Each node's `cluster_status` lists only the peers it can reach, so the health-gate and control-plane collector query EVERY node and compare `running_nodes` against the expected size (see ADR-0017, ADR-0023). On RabbitMQ 4.3 the `partitions` field of `cluster_status` is always empty (Khepri hard-codes it), so the membership check is the signal that actually catches a split. The partition check stays as a fail-closed extra.

## References
- commits a900690, 55804b4, 978632a (host tuning + quorum migration)
- commit 534b27b (pin allowed upgrades)
- kolla/globals.yml (Messaging options, `rabbitmq_allowed_upgrades`, `rabbitmq_extra_config`); kolla/config/global.conf
- kolla-ansible commit 50bbcb09d "Don't allow quorum queues to be disabled": https://opendev.org/openstack/kolla-ansible/commit/50bbcb09d0d7cbff5858a9b8b416838ef25d1d1f
- oslo.messaging Launchpad bug 2126768 (stream fanout replica): https://bugs.launchpad.net/oslo.messaging/+bug/2126768
- kolla-ansible 2026.2 partition default: https://opendev.org/openstack/kolla-ansible/src/branch/stable/2026.2/ansible/roles/rabbitmq/defaults/main.yml#L67 and templates/rabbitmq.conf.j2#L9
- RabbitMQ 4.3+ partitions guide (`cluster_partition_handling` accepted, no effect): https://www.rabbitmq.com/docs/partitions
- Khepri `cli_cluster_status` returns `{partitions, []}`: https://github.com/rabbitmq/rabbitmq-server/blob/v4.3.7/deps/rabbit/src/rabbit_khepri.erl#L1159-L1169

#!/usr/bin/env python3
"""Control-plane landmine collector for the node_exporter textfile collector.

Writes a Prometheus textfile (.prom) with gauges for the two control-plane
landmines that exporter metrics express poorly, reusing the same docker-exec
CLI checks the health-gate Ansible role uses
(ansible/roles/health-gate/tasks/{rabbitmq,ovn}.yml):

  - RabbitMQ partition / membership under partition_handling=ignore. A node on
    the minority side of a split does NOT self-pause, so a single node's view
    can look healthy. Each controller runs this collector and emits its OWN
    node's view; PromQL aggregates across nodes (alert if ANY node sees a
    partition or fewer running nodes than expected). That is the only reliable
    split-brain signal on this fleet.
  - OVN stale/missing chassis (SB-DB set difference) and ovn-controller SB
    connection liveness. A Chassis row outlives a dead ovn-controller, so the
    set difference alone is not liveness -- each chassis host also probes its
    LOCAL ovn-controller via ovn-appctl, which is why the collector runs on
    every expected chassis host (the compute group, incl. compute-only nodes),
    not only the controllers.

FAIL SAFE (mirrors health-gate's refuse-rather-than-guess posture): if a CLI
call errors or returns unexpected output, the collector emits an explicit
*_check_failed 1 gauge and does NOT emit a healthy data value, so a broken
check can never read as healthy. The whole file is written atomically
(temp-file-then-rename) so node_exporter never scrapes a partial file. If the
collector dies entirely the file stops being refreshed and the
cpa_collector_last_run_timestamp_seconds gauge goes stale -- U4's collector-dead
alert catches that.

Which checks run on a host is decided by the Ansible role from inventory group
membership and passed in via environment variables (see the role's service
unit template); an unset container variable means "this host does not run that
container -- correctly skip" (not a failure).

Run with --check for an offline self-test of the parsers (no docker needed).
"""

import os
import subprocess
import sys
import tempfile
import time

# Metric name prefix: cpa = control-plane alerts.
PREFIX = "cpa"


def run_cmd(argv: list[str]) -> str:
    """Run a command, returning stdout. Raises on non-zero exit or timeout."""
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=30, check=True
    )
    return result.stdout


# --------------------------------------------------------------------------
# Pure parsers (unit-tested by --check; no docker dependency)
# --------------------------------------------------------------------------

def parse_rabbitmq(json_text: str) -> tuple[int, int]:
    """Parse `rabbitmqctl cluster_status --formatter json`.

    Returns (running_node_count, partition_count). Raises ValueError if the
    JSON lacks running_nodes/partitions -- a formatter schema change must
    refuse, not default to a vacuously-healthy 0 (mirrors health-gate
    rabbitmq.yml schema_ok guard).
    """
    import json

    parsed = json.loads(json_text)
    if "running_nodes" not in parsed or "partitions" not in parsed:
        raise ValueError("rabbitmqctl JSON missing running_nodes/partitions")
    running = parsed["running_nodes"] or []
    partitions = parsed["partitions"] or []
    return len(running), len(partitions)


def parse_ovn_chassis(stdout: str, expected: list[str]) -> tuple[int, int]:
    """Compare live SB-DB chassis hostnames against the expected chassis set.

    Returns (missing_count, stale_count): missing = expected hosts with no live
    chassis, stale = chassis rows with no matching expected host. Mirrors
    health-gate ovn.yml.
    """
    live = [line.strip() for line in stdout.splitlines() if line.strip()]
    missing = set(expected) - set(live)
    stale = set(live) - set(expected)
    return len(missing), len(stale)


def parse_ovn_connection(stdout: str) -> bool:
    """ovn-appctl connection-status is live only on an exact 'connected'
    ('not connected' contains the substring, so match exactly)."""
    return stdout.strip() == "connected"


# --------------------------------------------------------------------------
# Metric builders (each catches its own failure -> *_check_failed 1)
# --------------------------------------------------------------------------

def _g(name: str, value, node: str, extra: str = "") -> str:
    labels = f'node="{node}"' + (f",{extra}" if extra else "")
    return f"{PREFIX}_{name}{{{labels}}} {value}"


def collect_rabbitmq(node: str, container: str, expected_size: int) -> list[str]:
    try:
        out = run_cmd(
            ["docker", "exec", container, "rabbitmqctl", "cluster_status",
             "--formatter", "json"]
        )
        running, partitions = parse_rabbitmq(out)
        return [
            _g("rabbitmq_running_nodes", running, node),
            _g("rabbitmq_expected_nodes", expected_size, node),
            _g("rabbitmq_partitions", partitions, node),
            _g("rabbitmq_check_failed", 0, node),
        ]
    except Exception:  # noqa: BLE001 -- fail safe: never emit a healthy value
        return [_g("rabbitmq_check_failed", 1, node)]


def collect_ovn_chassis(node: str, container: str, expected: list[str]) -> list[str]:
    try:
        out = run_cmd(
            ["docker", "exec", container, "ovn-sbctl", "--no-leader-only",
             "--columns=hostname", "--bare", "list", "Chassis"]
        )
        missing, stale = parse_ovn_chassis(out, expected)
        return [
            _g("ovn_chassis_missing", missing, node),
            _g("ovn_chassis_stale", stale, node),
            _g("ovn_chassis_check_failed", 0, node),
        ]
    except Exception:  # noqa: BLE001
        return [_g("ovn_chassis_check_failed", 1, node)]


def collect_ovn_controller(node: str, container: str) -> list[str]:
    try:
        out = run_cmd(
            ["docker", "exec", container, "ovn-appctl", "-t", "ovn-controller",
             "connection-status"]
        )
        connected = 1 if parse_ovn_connection(out) else 0
        return [
            _g("ovn_controller_connected", connected, node),
            _g("ovn_controller_check_failed", 0, node),
        ]
    except Exception:  # noqa: BLE001
        return [_g("ovn_controller_check_failed", 1, node)]


HELP_LINES = [
    f"# HELP {PREFIX}_rabbitmq_running_nodes Running RabbitMQ nodes this node sees.",
    f"# TYPE {PREFIX}_rabbitmq_running_nodes gauge",
    f"# HELP {PREFIX}_rabbitmq_expected_nodes Expected RabbitMQ cluster size.",
    f"# TYPE {PREFIX}_rabbitmq_expected_nodes gauge",
    f"# HELP {PREFIX}_rabbitmq_partitions Network partitions this node reports.",
    f"# TYPE {PREFIX}_rabbitmq_partitions gauge",
    f"# HELP {PREFIX}_rabbitmq_check_failed 1 if the RabbitMQ check could not run.",
    f"# TYPE {PREFIX}_rabbitmq_check_failed gauge",
    f"# HELP {PREFIX}_ovn_chassis_missing Expected chassis hosts with no live SB row.",
    f"# TYPE {PREFIX}_ovn_chassis_missing gauge",
    f"# HELP {PREFIX}_ovn_chassis_stale SB chassis rows with no matching host.",
    f"# TYPE {PREFIX}_ovn_chassis_stale gauge",
    f"# HELP {PREFIX}_ovn_chassis_check_failed 1 if the OVN chassis check could not run.",
    f"# TYPE {PREFIX}_ovn_chassis_check_failed gauge",
    f"# HELP {PREFIX}_ovn_controller_connected 1 if ovn-controller SB status is connected.",
    f"# TYPE {PREFIX}_ovn_controller_connected gauge",
    f"# HELP {PREFIX}_ovn_controller_check_failed 1 if the ovn-controller probe could not run.",
    f"# TYPE {PREFIX}_ovn_controller_check_failed gauge",
    f"# HELP {PREFIX}_collector_last_run_timestamp_seconds Unix time of the last collector run.",
    f"# TYPE {PREFIX}_collector_last_run_timestamp_seconds gauge",
]


def build_metrics(env: dict) -> list[str]:
    node = env.get("CPA_NODE") or os.uname().nodename
    lines: list[str] = list(HELP_LINES)

    rabbitmq_container = env.get("CPA_RABBITMQ_CONTAINER", "").strip()
    if rabbitmq_container:
        expected_size = int(env.get("CPA_RABBITMQ_EXPECTED_SIZE", "0"))
        lines += collect_rabbitmq(node, rabbitmq_container, expected_size)

    ovn_sb_container = env.get("CPA_OVN_SB_CONTAINER", "").strip()
    if ovn_sb_container:
        expected = [
            h.strip() for h in env.get("CPA_EXPECTED_CHASSIS", "").split(",") if h.strip()
        ]
        lines += collect_ovn_chassis(node, ovn_sb_container, expected)

    ovn_controller_container = env.get("CPA_OVN_CONTROLLER_CONTAINER", "").strip()
    if ovn_controller_container:
        lines += collect_ovn_controller(node, ovn_controller_container)

    lines.append(_g("collector_last_run_timestamp_seconds", int(time.time()), node))
    return lines


def write_atomic(path: str, lines: list[str]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(tmp, path)  # atomic on the same filesystem
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------
# Offline self-test (--check): exercises the parsers without docker.
# --------------------------------------------------------------------------

def self_check() -> int:
    # RabbitMQ: clean full cluster.
    assert parse_rabbitmq('{"running_nodes":["a","b","c"],"partitions":[]}') == (3, 0)
    # RabbitMQ: partitioned + shrunk view (the partition_handling=ignore case).
    assert parse_rabbitmq('{"running_nodes":["a"],"partitions":["b"]}') == (1, 1)
    # RabbitMQ: schema change -> must raise, not pass vacuously.
    try:
        parse_rabbitmq('{"nodes":["a","b","c"]}')
    except ValueError:
        pass
    else:
        raise AssertionError("schema-changed RabbitMQ JSON must raise")

    expected = ["openstack01", "openstack02", "openstack04", "openstack05"]
    # OVN: all chassis live, none stale.
    assert parse_ovn_chassis(
        "openstack01\nopenstack02\nopenstack04\nopenstack05\n", expected
    ) == (0, 0)
    # OVN: openstack05 missing, plus a stale row for a removed host.
    assert parse_ovn_chassis(
        "openstack01\nopenstack02\nopenstack04\nopenstack99\n", expected
    ) == (1, 1)

    # ovn-controller liveness: exact match only.
    assert parse_ovn_connection("connected\n") is True
    assert parse_ovn_connection("not connected\n") is False

    # Fail-safe: a failing check emits *_check_failed 1 and no healthy value.
    failed = collect_rabbitmq("openstack01", "no_such_container", 3)
    assert failed == [_g("rabbitmq_check_failed", 1, "openstack01")], failed

    print("self-check OK")
    return 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return self_check()
    output_file = os.environ.get(
        "CPA_OUTPUT_FILE",
        "/var/lib/node_exporter/textfile_collector/control-plane-landmines.prom",
    )
    write_atomic(output_file, build_metrics(dict(os.environ)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

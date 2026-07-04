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
  - Keystone failed-auth volume. lockout_failure_attempts throttles brute force
    per account but emits no operator-visible signal; a spray across many
    accounts is invisible. Each controller counts 401 responses to
    POST /v3/auth/tokens in a bounded tail of its local keystone-uwsgi log
    within a trailing window. The logged client IP is haproxy's internal
    address, not the real client, so this is a volume-spike detector only --
    it cannot attribute failures to a source.

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

import calendar
import collections
import os
import re
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


# Failed password auth in the keystone uWSGI request log: a 401 on POST
# /v3/auth/tokens. The status is anchored inside its parentheses -- a bare
# " 401 " grep false-matches byte counts like "401 bytes" in unrelated lines.
# GET /v3/auth/tokens 401 is deliberately excluded: that is token validation
# with an expired/invalid token, which is routine noise, not a password spray.
# A query string (e.g. ?nocatalog, sent by some SDK clients) may follow the
# path and must still count. uWSGI timestamp: asctime with a space-padded day,
# e.g. [Sat Jul  4 07:13:09 2026], in UTC on this fleet. strptime's %a/%b are
# locale-dependent; the systemd unit runs under the C locale, and a non-English
# locale would surface as check_failed rather than a silent miscount.
_UWSGI_TS_FORMAT = "%a %b %d %H:%M:%S %Y"
_UWSGI_TS_PATTERN = r"\w{3} \w{3} [ \d]\d \d\d:\d\d:\d\d \d{4}"
_KEYSTONE_AUTH_FAIL_RE = re.compile(
    rf"\[(?P<ts>{_UWSGI_TS_PATTERN})\] "
    r"POST /v3/auth/tokens(\?\S*)? .*\(HTTP/1\.[01] 401\)"
)
_UWSGI_TS_RE = re.compile(rf"\[(?P<ts>{_UWSGI_TS_PATTERN})\]")
# Broad request-line shape (any method/status). If a non-empty tail matches
# NOTHING here, the uWSGI log format has drifted and the count regex above
# would report a trusted-but-wrong 0 -- refuse instead (check_failed).
# Healthcheck lines match this too, so a quiet-but-healthy log stays valid.
_UWSGI_REQUEST_RE = re.compile(r"\(HTTP/1\.[01] \d{3}\)")


def count_keystone_auth_failures(lines: list[str], now: float, window: int) -> int:
    """Count failed password auths (401 POST /v3/auth/tokens) within the
    trailing window ending at `now` (both UTC epoch seconds). Future
    timestamps (clock skew / malformed lines) never count."""
    count = 0
    for line in lines:
        match = _KEYSTONE_AUTH_FAIL_RE.search(line)
        if not match:
            continue
        age = now - calendar.timegm(time.strptime(match.group("ts"), _UWSGI_TS_FORMAT))
        if 0 <= age <= window:
            count += 1
    return count


def _earliest_uwsgi_ts(lines: list[str]) -> float | None:
    """UTC epoch of the first parseable uWSGI timestamp. Lines are
    chronological, so this is the tail's coverage horizon."""
    for line in lines:
        match = _UWSGI_TS_RE.search(line)
        if not match:
            continue
        try:
            return float(
                calendar.timegm(time.strptime(match.group("ts"), _UWSGI_TS_FORMAT))
            )
        except ValueError:
            continue
    return None


def _tail(path: str, maxlen: int) -> list[str]:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return list(collections.deque(handle, maxlen=maxlen))


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


def collect_keystone_auth(node: str, log_path: str, window, tail_lines) -> list[str]:
    # Bounded tail, not a byte offset or monotonic counter: offsets reset on
    # rotation, and the deque bound caps memory on a huge log. A bounded tail
    # alone is offset-safe but NOT window-safe: right after logrotate the
    # in-window failures live in <log>.1, so when the current file's tail does
    # not cover the full window, a bounded tail of <log>.1 is prepended.
    # window/tail_lines arrive as env strings and are validated HERE so a bad
    # override (non-int, zero, negative) surfaces as check_failed 1, never as
    # a trusted zero-line scan.
    try:
        window = int(window)
        tail_lines = int(tail_lines)
        if window <= 0 or tail_lines <= 0:
            raise ValueError("window and tail_lines must be positive")
        now = time.time()
        current = _tail(log_path, tail_lines)
        earliest = _earliest_uwsgi_ts(current)
        lines = current
        if not current or (earliest is not None and now - earliest < window):
            rotated = f"{log_path}.1"
            if os.path.exists(rotated):
                lines = _tail(rotated, tail_lines) + current
        if lines and not any(_UWSGI_REQUEST_RE.search(line) for line in lines):
            # Format drift: a non-empty tail with zero recognizable request
            # lines means the count regex would report a trusted-but-wrong 0.
            raise ValueError("no recognizable uWSGI request lines in log tail")
        failures = count_keystone_auth_failures(lines, now, window)
        # Saturated tail whose oldest line is still in-window: the window is
        # not fully covered, so the count is a floor, not a total -- surface
        # check_failed alongside it so the undercount is never trusted
        # silently. (Saturating 20k lines in 10 minutes is itself attack-scale
        # volume, so the count still carries signal.)
        saturated = (
            len(current) == tail_lines
            and earliest is not None
            and 0 <= now - earliest <= window
        )
        return [
            _g("keystone_auth_failures_recent", failures, node),
            _g("keystone_auth_check_failed", 1 if saturated else 0, node),
        ]
    except Exception:  # noqa: BLE001 -- fail safe: never emit a healthy value
        return [_g("keystone_auth_check_failed", 1, node)]


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
    f"# HELP {PREFIX}_keystone_auth_failures_recent Failed password auths (401 POST /v3/auth/tokens) in the trailing window.",
    f"# TYPE {PREFIX}_keystone_auth_failures_recent gauge",
    f"# HELP {PREFIX}_keystone_auth_check_failed 1 if the keystone auth-failure check could not run or its count is an incomplete floor (saturated tail).",
    f"# TYPE {PREFIX}_keystone_auth_check_failed gauge",
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

    keystone_log = env.get("CPA_KEYSTONE_LOG", "").strip()
    if keystone_log:
        # Raw strings on purpose: collect_keystone_auth validates them inside
        # its fail-safe, so a bad override reads as check_failed, not a crash.
        lines += collect_keystone_auth(
            node,
            keystone_log,
            env.get("CPA_KEYSTONE_WINDOW_SECONDS", "600"),
            env.get("CPA_KEYSTONE_TAIL_LINES", "20000"),
        )

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

    # Keystone: in-window 401 POST counts; GET / non-401 / "401 bytes" do not.
    now = calendar.timegm(time.strptime("Sat Jul  4 07:15:00 2026", "%a %b %d %H:%M:%S %Y"))
    uwsgi = (
        "[pid: 22|app: 0|req: 1/1] 192.168.113.14 () {36 vars in 461 bytes} "
        "[Sat Jul  4 07:13:09 2026] %s => generated 109 bytes in 38 msecs "
        "(HTTP/1.1 %s) 6 headers in 262 bytes (1 switches on core 0)"
    )
    assert count_keystone_auth_failures(
        [
            uwsgi % ("POST /v3/auth/tokens", "401"),   # counts
            uwsgi % ("POST /v3/auth/tokens", "201"),   # success -- no
            uwsgi % ("GET /v3/auth/tokens", "401"),    # token validation -- no
            # Byte-count trap: "401 bytes" on a successful POST must not count.
            (uwsgi % ("POST /v3/auth/tokens", "201")).replace("109 bytes", "401 bytes"),
        ],
        now, 600,
    ) == 1
    # Out-of-window timestamp excluded.
    old = uwsgi % ("POST /v3/auth/tokens", "401")
    assert count_keystone_auth_failures([old], now + 3600, 600) == 0
    # Future timestamp (clock skew) excluded.
    assert count_keystone_auth_failures([old], now - 3600, 600) == 0

    # Fail-safe: a failing check emits *_check_failed 1 and no healthy value.
    failed = collect_rabbitmq("openstack01", "no_such_container", 3)
    assert failed == [_g("rabbitmq_check_failed", 1, "openstack01")], failed
    failed = collect_keystone_auth("openstack01", "/no/such/log", 600, 20000)
    assert failed == [_g("keystone_auth_check_failed", 1, "openstack01")], failed

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

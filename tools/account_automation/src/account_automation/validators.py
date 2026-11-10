import logging
import re

from account_automation.duration import DURATION_MAP
from account_automation.models import SheetRow, Status


LOGGER = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
# Keystone IDs are uuid4().hex. The tool's own lookups are name-only, so this is
# deliberate defence in depth: an ID-shaped name is still taken for an ID by
# `openstack project show <name>` and any other find-by-ID-first client.
ID_LIKE_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
# Names already held by the cloud's own Keystone objects in the shared domain:
# the admin user/project, the service project, and Kolla-Ansible's default
# service users (``<service>_keystone_user``). Compared case-insensitively. This
# is the only list; the ownership tag check in keystone_accounts is the backstop
# for anything missing from it.
RESERVED_USERNAMES = frozenset({
    "admin", "service", "default",
    "aodh", "barbican", "blazar", "ceilometer", "ceph_rgw", "cinder", "cloudkitty",
    "cyborg", "designate", "glance", "gnocchi", "heat", "heat_domain_admin", "ironic",
    "ironic-inspector", "ironic_inspector", "keystone", "kuryr", "magnum",
    "magnum_trustee_domain_admin", "manila", "masakari", "mistral", "neutron", "nova",
    "octavia", "placement", "skyline", "swift", "tacker", "trove", "venus", "watcher",
    "zun",
})
RECOGNIZED_EXTRAS = frozenset({"Load Balancer", "GPU"})
EXTRA_ALIASES = {"負載平衡器 (Load Balancer)": "Load Balancer"}

# RFC 5322-lite address check: dot-separated local part of permitted atext
# characters, then a domain of one or more dot-separated labels ending in a TLD.
# Deliberately conservative -- it rejects the obviously malformed without trying
# to model the full grammar (quoted strings, comments, IP literals).
EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
    r"(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
# RFC 5321 size limits: 254 chars for the whole address, 64 for the local part.
MAX_EMAIL_LENGTH = 254
MAX_LOCAL_PART_LENGTH = 64


def is_valid_email(value: str) -> bool:
    if not value or len(value) > MAX_EMAIL_LENGTH:
        return False
    local_part, _, _ = value.partition("@")
    if len(local_part) > MAX_LOCAL_PART_LENGTH:
        return False
    return EMAIL_PATTERN.fullmatch(value) is not None


def new_username_problem(username: str) -> str:
    """Return why ``username`` must not name a new account, or "" if it may."""
    if not USERNAME_PATTERN.fullmatch(username):
        return "Invalid username"
    if username.lower() in RESERVED_USERNAMES:
        return "Reserved username"
    if ID_LIKE_PATTERN.fullmatch(username):
        return "Username looks like an OpenStack ID"
    return ""


def validate_row(row: SheetRow) -> tuple[bool, str]:
    # Reserved/ID-like names only gate new accounts: rows already past APPROVED
    # must stay processable (expire, disable, delete); the ownership tag guards those.
    if row.status is Status.APPROVED:
        problem = new_username_problem(row.username)
    else:
        problem = "" if USERNAME_PATTERN.fullmatch(row.username) else "Invalid username"
    if problem:
        return False, problem

    if not is_valid_email(row.email):
        return False, "Invalid email"

    if row.duration_raw not in DURATION_MAP:
        return False, "Unknown duration"

    quota_values = (row.quota.vcpus, row.quota.ram_gb, row.quota.storage_gb)
    if any(value is not None and value <= 0 for value in quota_values):
        return False, "Quota values must be positive"

    return True, ""


def parse_status(raw: str) -> Status | None:
    """Return the Status for ``raw`` (by value or name), or None. Never logs."""
    normalized = raw.strip()
    try:
        return Status(normalized.lower())
    except ValueError:
        return Status.__members__.get(normalized.upper())


def validate_status(raw: str, row_number: int | None = None) -> Status | None:
    status = parse_status(raw)
    if status is None and raw.strip():
        # Not counted as an invalid row: the sheet may hold statuses the
        # tool deliberately ignores (e.g. a hand-written "rejected").
        LOGGER.warning("Unknown status %r row_number=%s — skipping row", raw, row_number)
    return status


def validate_extras(raw: str) -> frozenset[str]:
    stripped = [part.strip() for part in raw.split(",") if part.strip()]
    extras = frozenset(EXTRA_ALIASES.get(s, s) for s in stripped)
    unknown = sorted(extra for extra in extras if extra not in RECOGNIZED_EXTRAS)
    if unknown:
        LOGGER.warning("Unrecognized extras: %s", ", ".join(unknown))
    return extras

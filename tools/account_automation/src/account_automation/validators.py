import logging
import re

from account_automation.duration import DURATION_MAP
from account_automation.models import SheetRow, Status


LOGGER = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
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


def validate_row(row: SheetRow) -> tuple[bool, str]:
    if not USERNAME_PATTERN.fullmatch(row.username):
        return False, "Invalid username"

    if not is_valid_email(row.email):
        return False, "Invalid email"

    if row.duration_raw not in DURATION_MAP:
        return False, "Unknown duration"

    quota_values = (row.quota.vcpus, row.quota.ram_gb, row.quota.storage_gb)
    if any(value is not None and value <= 0 for value in quota_values):
        return False, "Quota values must be positive"

    return True, ""


def validate_status(raw: str) -> Status | None:
    normalized = raw.strip()
    if normalized == "":
        return None

    try:
        return Status(normalized.lower())
    except ValueError:
        try:
            return Status[normalized.upper()]
        except KeyError:
            LOGGER.warning("Unknown status: %s — skipping row", raw)
            return None


def validate_extras(raw: str) -> frozenset[str]:
    stripped = [part.strip() for part in raw.split(",") if part.strip()]
    extras = frozenset(EXTRA_ALIASES.get(s, s) for s in stripped)
    unknown = sorted(extra for extra in extras if extra not in RECOGNIZED_EXTRAS)
    if unknown:
        LOGGER.warning("Unrecognized extras: %s", ", ".join(unknown))
    return extras

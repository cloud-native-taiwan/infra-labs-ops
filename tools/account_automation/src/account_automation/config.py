from dataclasses import dataclass
from os import getenv

from dotenv import load_dotenv


ENV_PREFIX = "INFRA_LABS_"


@dataclass(frozen=True)
class AppConfig:
    google_service_account_json: str
    spreadsheet_id: str
    worksheet_name: str = "Sheet1"
    openstack_cloud: str = "default"
    openstack_domain_id: str = ""
    openstack_member_role: str = "member"
    openstack_lb_role: str = "load-balancer_member"
    resend_api_key: str = ""
    resend_from_email: str = ""
    admin_email: str = ""
    expiry_warning_days: int = 14
    grace_period_days: int = 7
    dry_run: bool = False
    log_level: str = "INFO"


def _get_required(name: str) -> str:
    value = getenv(f"{ENV_PREFIX}{name}")
    if value is None or value.strip() == "":
        raise ValueError(f"{ENV_PREFIX}{name}")
    return value


def _get_optional(name: str, default: str) -> str:
    value = getenv(f"{ENV_PREFIX}{name}")
    if value is None or value.strip() == "":
        return default
    return value


def _get_int(name: str, default: int) -> int:
    raw = getenv(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _get_bool(name: str, default: bool) -> bool:
    raw = getenv(f"{ENV_PREFIX}{name}")
    if raw is None or raw.strip() == "":
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {ENV_PREFIX}{name}: {raw}")


def _is_empty(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _get_field(name: str, require_all: bool) -> str:
    return _get_required(name) if require_all else _get_optional(name, "")


def load_config(require_all: bool = True) -> AppConfig:
    load_dotenv()

    required_names = ["OPENSTACK_DOMAIN_ID"]
    if require_all:
        required_names.extend(
            [
                "GOOGLE_SERVICE_ACCOUNT_JSON",
                "SPREADSHEET_ID",
                "RESEND_API_KEY",
                "RESEND_FROM_EMAIL",
            ]
        )
    missing = [
        f"{ENV_PREFIX}{name}"
        for name in required_names
        if _is_empty(getenv(f"{ENV_PREFIX}{name}"))
    ]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(sorted(missing))}"
        )

    config = AppConfig(
        google_service_account_json=_get_field("GOOGLE_SERVICE_ACCOUNT_JSON", require_all),
        spreadsheet_id=_get_field("SPREADSHEET_ID", require_all),
        worksheet_name=_get_optional("WORKSHEET_NAME", "Sheet1"),
        openstack_cloud=_get_optional("OPENSTACK_CLOUD", "default"),
        openstack_domain_id=_get_required("OPENSTACK_DOMAIN_ID"),
        openstack_member_role=_get_optional("OPENSTACK_MEMBER_ROLE", "member"),
        openstack_lb_role=_get_optional("OPENSTACK_LB_ROLE", "load-balancer_member"),
        resend_api_key=_get_field("RESEND_API_KEY", require_all),
        resend_from_email=_get_field("RESEND_FROM_EMAIL", require_all),
        admin_email=_get_optional("ADMIN_EMAIL", ""),
        expiry_warning_days=_get_int("EXPIRY_WARNING_DAYS", 14),
        grace_period_days=_get_int("GRACE_PERIOD_DAYS", 7),
        dry_run=_get_bool("DRY_RUN", False),
        log_level=_get_optional("LOG_LEVEL", "INFO"),
    )

    if config.admin_email != "":
        for entry in config.admin_email.split(","):
            email = entry.strip()
            if "@" not in email:
                raise ValueError(f"Invalid email in admin_email: {entry}")

    return config

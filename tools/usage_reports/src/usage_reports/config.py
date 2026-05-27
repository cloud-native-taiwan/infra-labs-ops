from dataclasses import dataclass
from os import getenv

from dotenv import load_dotenv


ENV_PREFIX = "INFRA_LABS_"


@dataclass(frozen=True)
class AppConfig:
    openstack_cloud: str = "openstack"
    cloudkitty_endpoint_override: str = ""
    resend_api_key: str = ""
    resend_from_email: str = ""
    admin_email: str = ""
    report_timezone: str = "Asia/Taipei"
    delivery_manifest_path: str = "/var/lib/usage-reports/deliveries.json"
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

    if require_all:
        required_names = ["RESEND_API_KEY", "RESEND_FROM_EMAIL"]
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
        openstack_cloud=_get_optional("OPENSTACK_CLOUD", "openstack"),
        cloudkitty_endpoint_override=_get_optional("CLOUDKITTY_ENDPOINT_OVERRIDE", ""),
        resend_api_key=_get_field("RESEND_API_KEY", require_all),
        resend_from_email=_get_field("RESEND_FROM_EMAIL", require_all),
        admin_email=_get_optional("ADMIN_EMAIL", ""),
        report_timezone=_get_optional("REPORT_TIMEZONE", "Asia/Taipei"),
        delivery_manifest_path=_get_optional(
            "DELIVERY_MANIFEST_PATH", "/var/lib/usage-reports/deliveries.json"
        ),
        dry_run=_get_bool("DRY_RUN", False),
        log_level=_get_optional("LOG_LEVEL", "INFO"),
    )

    if config.admin_email != "":
        for entry in config.admin_email.split(","):
            email = entry.strip()
            if "@" not in email:
                raise ValueError(f"Invalid email in admin_email: {entry}")

    return config

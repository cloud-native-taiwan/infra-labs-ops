from dataclasses import dataclass
from os import getenv

from dotenv import load_dotenv

from infra_labs_common.env_config import (
    ENV_PREFIX,
    get_bool,
    get_field,
    get_optional,
    is_empty,
)


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


def load_config(require_all: bool = True) -> AppConfig:
    load_dotenv()

    if require_all:
        required_names = ["RESEND_API_KEY", "RESEND_FROM_EMAIL"]
        missing = [
            f"{ENV_PREFIX}{name}"
            for name in required_names
            if is_empty(getenv(f"{ENV_PREFIX}{name}"))
        ]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(sorted(missing))}"
            )

    config = AppConfig(
        openstack_cloud=get_optional("OPENSTACK_CLOUD", "openstack"),
        cloudkitty_endpoint_override=get_optional("CLOUDKITTY_ENDPOINT_OVERRIDE", ""),
        resend_api_key=get_field("RESEND_API_KEY", require_all),
        resend_from_email=get_field("RESEND_FROM_EMAIL", require_all),
        admin_email=get_optional("ADMIN_EMAIL", ""),
        report_timezone=get_optional("REPORT_TIMEZONE", "Asia/Taipei"),
        delivery_manifest_path=get_optional(
            "DELIVERY_MANIFEST_PATH", "/var/lib/usage-reports/deliveries.json"
        ),
        dry_run=get_bool("DRY_RUN", False),
        log_level=get_optional("LOG_LEVEL", "INFO"),
    )

    if config.admin_email != "":
        for entry in config.admin_email.split(","):
            email = entry.strip()
            if "@" not in email:
                raise ValueError(f"Invalid email in admin_email: {entry}")

    return config

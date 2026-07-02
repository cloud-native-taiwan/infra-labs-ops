from dataclasses import dataclass
from os import getenv

from dotenv import load_dotenv

from infra_labs_common.env_config import (
    ENV_PREFIX,
    get_bool,
    get_field,
    get_int,
    get_optional,
    get_required,
    is_empty,
)

from account_automation.validators import is_valid_email


@dataclass(frozen=True)
class AppConfig:
    google_service_account_json: str
    spreadsheet_id: str
    worksheet_name: str = "Sheet1"
    openstack_cloud: str = "openstack"
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
    rgw_admin_url: str = ""
    rgw_admin_access_key: str = ""
    rgw_admin_secret_key: str = ""
    rgw_admin_region: str = ""


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
        if is_empty(getenv(f"{ENV_PREFIX}{name}"))
    ]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(sorted(missing))}"
        )

    config = AppConfig(
        google_service_account_json=get_field("GOOGLE_SERVICE_ACCOUNT_JSON", require_all),
        spreadsheet_id=get_field("SPREADSHEET_ID", require_all),
        worksheet_name=get_optional("WORKSHEET_NAME", "Sheet1"),
        openstack_cloud=get_optional("OPENSTACK_CLOUD", "openstack"),
        openstack_domain_id=get_required("OPENSTACK_DOMAIN_ID"),
        openstack_member_role=get_optional("OPENSTACK_MEMBER_ROLE", "member"),
        openstack_lb_role=get_optional("OPENSTACK_LB_ROLE", "load-balancer_member"),
        resend_api_key=get_field("RESEND_API_KEY", require_all),
        resend_from_email=get_field("RESEND_FROM_EMAIL", require_all),
        admin_email=get_optional("ADMIN_EMAIL", ""),
        expiry_warning_days=get_int("EXPIRY_WARNING_DAYS", 14),
        grace_period_days=get_int("GRACE_PERIOD_DAYS", 7),
        dry_run=get_bool("DRY_RUN", False),
        log_level=get_optional("LOG_LEVEL", "INFO"),
        rgw_admin_url=get_optional("RGW_ADMIN_URL", ""),
        rgw_admin_access_key=get_optional("RGW_ADMIN_ACCESS_KEY", ""),
        rgw_admin_secret_key=get_optional("RGW_ADMIN_SECRET_KEY", ""),
        rgw_admin_region=get_optional("RGW_ADMIN_REGION", ""),
    )

    if config.rgw_admin_url and not (config.rgw_admin_access_key and config.rgw_admin_secret_key):
        raise ValueError(
            "INFRA_LABS_RGW_ADMIN_ACCESS_KEY and INFRA_LABS_RGW_ADMIN_SECRET_KEY "
            "are required when INFRA_LABS_RGW_ADMIN_URL is set"
        )

    if config.admin_email != "":
        for entry in config.admin_email.split(","):
            email = entry.strip()
            if not is_valid_email(email):
                raise ValueError(f"Invalid email in admin_email: {entry}")

    return config

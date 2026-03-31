import pytest

from account_automation import config as config_module
from account_automation.config import AppConfig, load_config


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch, make_config) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    expected = make_config()
    env_vars = {
        "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON": expected.google_service_account_json,
        "INFRA_LABS_SPREADSHEET_ID": expected.spreadsheet_id,
        "INFRA_LABS_WORKSHEET_NAME": expected.worksheet_name,
        "INFRA_LABS_OPENSTACK_CLOUD": expected.openstack_cloud,
        "INFRA_LABS_OPENSTACK_DOMAIN_ID": expected.openstack_domain_id,
        "INFRA_LABS_OPENSTACK_MEMBER_ROLE": expected.openstack_member_role,
        "INFRA_LABS_OPENSTACK_LB_ROLE": expected.openstack_lb_role,
        "INFRA_LABS_RESEND_API_KEY": expected.resend_api_key,
        "INFRA_LABS_RESEND_FROM_EMAIL": expected.resend_from_email,
        "INFRA_LABS_EXPIRY_WARNING_DAYS": str(expected.expiry_warning_days),
        "INFRA_LABS_GRACE_PERIOD_DAYS": str(expected.grace_period_days),
        "INFRA_LABS_DRY_RUN": "true",
        "INFRA_LABS_LOG_LEVEL": expected.log_level,
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    config = load_config()

    assert config == AppConfig(
        google_service_account_json=expected.google_service_account_json,
        spreadsheet_id=expected.spreadsheet_id,
        worksheet_name=expected.worksheet_name,
        openstack_cloud=expected.openstack_cloud,
        openstack_domain_id=expected.openstack_domain_id,
        openstack_member_role=expected.openstack_member_role,
        openstack_lb_role=expected.openstack_lb_role,
        resend_api_key=expected.resend_api_key,
        resend_from_email=expected.resend_from_email,
        expiry_warning_days=expected.expiry_warning_days,
        grace_period_days=expected.grace_period_days,
        dry_run=True,
        log_level=expected.log_level,
    )


def test_load_config_raises_for_missing_required_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    required_keys = [
        "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON",
        "INFRA_LABS_SPREADSHEET_ID",
        "INFRA_LABS_OPENSTACK_DOMAIN_ID",
        "INFRA_LABS_RESEND_API_KEY",
        "INFRA_LABS_RESEND_FROM_EMAIL",
    ]

    for key in required_keys:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValueError, match="Missing required environment variables"):
        load_config()


def test_admin_email_optional(monkeypatch: pytest.MonkeyPatch, make_config) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    expected = make_config()
    for key, value in {
        "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON": expected.google_service_account_json,
        "INFRA_LABS_SPREADSHEET_ID": expected.spreadsheet_id,
        "INFRA_LABS_OPENSTACK_DOMAIN_ID": expected.openstack_domain_id,
        "INFRA_LABS_RESEND_API_KEY": expected.resend_api_key,
        "INFRA_LABS_RESEND_FROM_EMAIL": expected.resend_from_email,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("INFRA_LABS_ADMIN_EMAIL", raising=False)

    config = load_config()
    assert config.admin_email == ""


def test_admin_email_comma_separated(monkeypatch: pytest.MonkeyPatch, make_config) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    expected = make_config()
    for key, value in {
        "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON": expected.google_service_account_json,
        "INFRA_LABS_SPREADSHEET_ID": expected.spreadsheet_id,
        "INFRA_LABS_OPENSTACK_DOMAIN_ID": expected.openstack_domain_id,
        "INFRA_LABS_RESEND_API_KEY": expected.resend_api_key,
        "INFRA_LABS_RESEND_FROM_EMAIL": expected.resend_from_email,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("INFRA_LABS_ADMIN_EMAIL", "a@x.com,b@y.com")

    config = load_config()
    assert config.admin_email == "a@x.com,b@y.com"


def test_admin_email_invalid_raises(monkeypatch: pytest.MonkeyPatch, make_config) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    expected = make_config()
    for key, value in {
        "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON": expected.google_service_account_json,
        "INFRA_LABS_SPREADSHEET_ID": expected.spreadsheet_id,
        "INFRA_LABS_OPENSTACK_DOMAIN_ID": expected.openstack_domain_id,
        "INFRA_LABS_RESEND_API_KEY": expected.resend_api_key,
        "INFRA_LABS_RESEND_FROM_EMAIL": expected.resend_from_email,
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("INFRA_LABS_ADMIN_EMAIL", "invalid")

    with pytest.raises(ValueError, match="Invalid email in admin_email"):
        load_config()


def test_load_config_require_all_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("INFRA_LABS_OPENSTACK_DOMAIN_ID", "domain-id")
    for key in [
        "INFRA_LABS_GOOGLE_SERVICE_ACCOUNT_JSON",
        "INFRA_LABS_SPREADSHEET_ID",
        "INFRA_LABS_RESEND_API_KEY",
        "INFRA_LABS_RESEND_FROM_EMAIL",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = load_config(require_all=False)
    assert config.openstack_domain_id == "domain-id"
    assert config.google_service_account_json == ""

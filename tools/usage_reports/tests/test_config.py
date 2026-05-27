import pytest

from usage_reports import config as config_module
from usage_reports.config import AppConfig, load_config


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch, make_config) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    expected = make_config(dry_run=True)
    env = {
        "INFRA_LABS_OPENSTACK_CLOUD": expected.openstack_cloud,
        "INFRA_LABS_RESEND_API_KEY": expected.resend_api_key,
        "INFRA_LABS_RESEND_FROM_EMAIL": expected.resend_from_email,
        "INFRA_LABS_REPORT_TIMEZONE": expected.report_timezone,
        "INFRA_LABS_DELIVERY_MANIFEST_PATH": expected.delivery_manifest_path,
        "INFRA_LABS_DRY_RUN": "true",
        "INFRA_LABS_LOG_LEVEL": expected.log_level,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    config = load_config()
    assert config == expected


def test_load_config_missing_required_lists_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    for key in [
        "INFRA_LABS_RESEND_API_KEY",
        "INFRA_LABS_RESEND_FROM_EMAIL",
    ]:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ValueError) as exc:
        load_config()
    message = str(exc.value)
    assert "INFRA_LABS_RESEND_API_KEY" in message
    assert "INFRA_LABS_RESEND_FROM_EMAIL" in message


def test_load_config_partial_resend_creds_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("INFRA_LABS_RESEND_API_KEY", "re_secret")
    monkeypatch.delenv("INFRA_LABS_RESEND_FROM_EMAIL", raising=False)

    with pytest.raises(ValueError, match="INFRA_LABS_RESEND_FROM_EMAIL"):
        load_config()


def test_admin_email_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("INFRA_LABS_RESEND_API_KEY", "re_secret")
    monkeypatch.setenv("INFRA_LABS_RESEND_FROM_EMAIL", "a@b.com")
    monkeypatch.setenv("INFRA_LABS_ADMIN_EMAIL", "not-an-email")

    with pytest.raises(ValueError, match="Invalid email in admin_email"):
        load_config()


def test_admin_email_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("INFRA_LABS_RESEND_API_KEY", "re_secret")
    monkeypatch.setenv("INFRA_LABS_RESEND_FROM_EMAIL", "a@b.com")
    monkeypatch.setenv("INFRA_LABS_ADMIN_EMAIL", "ops@x.com,audit@y.com")

    config = load_config()
    assert config.admin_email == "ops@x.com,audit@y.com"


def test_load_config_require_all_false_allows_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    for key in [
        "INFRA_LABS_RESEND_API_KEY",
        "INFRA_LABS_RESEND_FROM_EMAIL",
    ]:
        monkeypatch.delenv(key, raising=False)

    config = load_config(require_all=False)
    assert config.resend_api_key == ""
    assert config.resend_from_email == ""


def test_bool_env_invalid_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("INFRA_LABS_RESEND_API_KEY", "re_secret")
    monkeypatch.setenv("INFRA_LABS_RESEND_FROM_EMAIL", "a@b.com")
    monkeypatch.setenv("INFRA_LABS_DRY_RUN", "maybe")

    with pytest.raises(ValueError, match="Invalid boolean value"):
        load_config()


def test_app_config_frozen() -> None:
    cfg = AppConfig()
    with pytest.raises(Exception):
        cfg.openstack_cloud = "other"  # type: ignore[misc]

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from usage_reports.models import (
    ProjectMember,
    ProjectUsage,
    ReportData,
    ReportPeriod,
    ResourceCost,
    ResourceKind,
)
from usage_reports.services.email_service import ResendEmailService, _admin_cc


def _period() -> ReportPeriod:
    return ReportPeriod(
        year=2026,
        month=5,
        begin_utc=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
        end_utc=datetime(2026, 5, 31, 16, 0, tzinfo=timezone.utc),
    )


def _report(resources: tuple[ResourceCost, ...]) -> ReportData:
    project = ProjectUsage(
        project_id="proj-1",
        project_name="<lab>alpha",  # intentional HTML chars to verify escaping
        resources=resources,
    )
    return ReportData(period=_period(), project=project)


def _recipient(email: str = "alice@example.com") -> ProjectMember:
    return ProjectMember(user_id="u-1", user_name="Alice", email=email)


def test_admin_cc_empty() -> None:
    assert _admin_cc("") == []


def test_admin_cc_multiple() -> None:
    assert _admin_cc("a@x.com,b@y.com") == ["a@x.com", "b@y.com"]


def test_render_includes_per_resource_breakdown(make_config) -> None:
    resources = (
        ResourceCost(
            kind=ResourceKind.INSTANCE,
            resource_id="vm-1",
            name="web-server",
            specs="4 vCPU / 8.0 GiB RAM",
            hours=720.0,
            cost=14.40,
            status="ACTIVE",
        ),
        ResourceCost(
            kind=ResourceKind.STORAGE,
            resource_id="vol-1",
            name="data",
            specs="100 GiB",
            hours=720.0,
            cost=0.72,
            status="in-use",
        ),
    )
    service = ResendEmailService(config=make_config())
    html = service._render(_report(resources), _recipient())

    assert "web-server" in html
    assert "data" in html
    assert "4 vCPU / 8.0 GiB RAM" in html
    assert "100 GiB" in html
    assert "14.4000" in html
    assert "0.7200" in html
    assert "15.1200" in html  # project total = 14.40 + 0.72
    assert "USD" in html  # costs are labelled with the currency


def test_render_ae1_different_cpu_gen_rates(make_config) -> None:
    """AE1: project with two VMs on different CPU generations shows
    different per-hour rates (here surfaced as the per-resource costs
    drawn from different hashmap mappings)."""
    resources = (
        ResourceCost(
            kind=ResourceKind.INSTANCE,
            resource_id="vm-newer",
            name="vm-newer",
            specs="4 vCPU / 8.0 GiB RAM (gen-newer)",
            hours=24.0,
            cost=4.80,
            status="ACTIVE",
        ),
        ResourceCost(
            kind=ResourceKind.INSTANCE,
            resource_id="vm-legacy",
            name="vm-legacy",
            specs="4 vCPU / 8.0 GiB RAM (gen-legacy)",
            hours=24.0,
            cost=1.20,
            status="ACTIVE",
        ),
    )
    service = ResendEmailService(config=make_config())
    html = service._render(_report(resources), _recipient())

    assert "4.8000" in html
    assert "1.2000" in html
    assert "gen-newer" in html
    assert "gen-legacy" in html


def test_render_escapes_html_in_user_data(make_config) -> None:
    resources = (
        ResourceCost(
            kind=ResourceKind.INSTANCE,
            resource_id="vm-1",
            name="<script>alert('x')</script>",
            specs="",
            hours=1.0,
            cost=0.01,
        ),
    )
    service = ResendEmailService(config=make_config())
    html = service._render(_report(resources), _recipient())
    # Jinja2 autoescape converts < and > to entities; tags must not appear raw
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # project_name also escaped
    assert "&lt;lab&gt;alpha" in html


def test_render_empty_resources(make_config) -> None:
    service = ResendEmailService(config=make_config())
    html = service._render(_report(()), _recipient())
    assert "No billable resources" in html


def test_send_dry_run_skips_resend(make_config) -> None:
    config = make_config(dry_run=True)
    service = ResendEmailService(config=config)
    with patch("usage_reports.services.email_service.resend") as mock_resend:
        mock_resend.Emails.send = MagicMock()
        service.send_cost_report(_report(()), _recipient())
        mock_resend.Emails.send.assert_not_called()


def test_send_calls_resend_with_payload(make_config) -> None:
    config = make_config(admin_email="ops@x.com")
    service = ResendEmailService(config=config)
    with patch("usage_reports.services.email_service.resend") as mock_resend:
        sent = MagicMock()
        mock_resend.Emails.send = sent
        service.send_cost_report(_report(()), _recipient())
        sent.assert_called_once()
        payload = sent.call_args.args[0]
        assert payload["to"] == "alice@example.com"
        assert payload["cc"] == ["ops@x.com"]
        assert "2026-05" in payload["subject"]


def test_send_failure_retries_then_raises(make_config) -> None:
    service = ResendEmailService(config=make_config())
    with patch("usage_reports.services.email_service.resend") as mock_resend:
        mock_resend.Emails.send.side_effect = RuntimeError("boom")
        with pytest.raises(RuntimeError, match="boom"):
            service.send_cost_report(_report(()), _recipient())
        # STANDARD_RETRY attempts 3 times
        assert mock_resend.Emails.send.call_count == 3

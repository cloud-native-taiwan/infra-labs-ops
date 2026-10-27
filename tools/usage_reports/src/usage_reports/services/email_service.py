"""Resend-backed email service for cost reports.

Mirrors the account_automation pattern: Jinja2 templates with
autoescape, dry_run guard, STANDARD_RETRY, admin CC for audit trail.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, cast

import resend
from jinja2 import Environment, PackageLoader, select_autoescape
from resend.exceptions import ValidationError

from usage_reports.config import AppConfig
from usage_reports.models import ProjectMember, ReportData
from usage_reports.retry import STANDARD_RETRY


LOGGER = logging.getLogger(__name__)

ADMIN_REPLY_TO = "infra@cloudnative.tw"


class RecipientRejectedError(ValueError):
    """Resend rejected the recipient (`to`) address. Rerunning cannot fix it.
    A ValueError, so STANDARD_RETRY does not retry it."""


class EmailService(Protocol):
    def send_cost_report(
        self, report: ReportData, recipient: ProjectMember, *, idempotency_key: str
    ) -> None: ...


class ResendEmailService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config
        resend.api_key = config.resend_api_key
        self._env = Environment(
            loader=PackageLoader("usage_reports", "templates"),
            autoescape=select_autoescape(["html"]),
        )

    @STANDARD_RETRY
    def send_cost_report(
        self, report: ReportData, recipient: ProjectMember, *, idempotency_key: str
    ) -> None:
        subject = (
            f"[CNTUG Infra Labs] {report.period.label} 使用量與成本報表 / Usage Cost Report"
        )
        html_body = self._render(report, recipient)

        if self._config.dry_run:
            LOGGER.info(
                "Dry run; not sending email project=%s month=%s recipient=%s",
                report.project.project_name or report.project.project_id,
                report.period.label,
                recipient.email,
            )
            return

        cc = _admin_cc(self._config.admin_email)
        payload: dict[str, Any] = {
            "from": self._config.resend_from_email,
            "to": recipient.email,
            "reply_to": ADMIN_REPLY_TO,
            "subject": subject,
            "html": html_body,
        }
        if cc:
            payload["cc"] = cc

        LOGGER.info(
            "Sending cost report project=%s month=%s recipient=%s",
            report.project.project_name or report.project.project_id,
            report.period.label,
            recipient.email,
        )
        try:
            resend.Emails.send(
                cast(Any, payload), cast(Any, {"idempotency_key": idempotency_key})
            )
        except ValidationError as exc:
            # Only a rejection that names the `to` field is this recipient's
            # problem. Sender, domain, and unrecognized validation errors hit
            # every send, so they stay ordinary (blocking) failures.
            if "`to`" in exc.message:
                raise RecipientRejectedError(exc.message) from exc
            raise

    def _render(self, report: ReportData, recipient: ProjectMember) -> str:
        template = self._env.get_template("report.html")
        return template.render(
            project=report.project,
            period=report.period,
            recipient_name=recipient.user_name or recipient.email,
        )


def _admin_cc(admin_email: str) -> list[str]:
    if not admin_email:
        return []
    return [addr.strip() for addr in admin_email.split(",") if addr.strip()]

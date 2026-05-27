"""Report orchestrator -- wires CloudKitty, OpenStack, and Resend services."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from usage_reports.config import AppConfig
from usage_reports.models import (
    ProjectUsage,
    ReportData,
    ReportPeriod,
    ResourceCost,
)
from usage_reports.services.cloudkitty_service import (
    CloudKittyService,
    CloudKittyServiceImpl,
)
from usage_reports.services.email_service import EmailService, ResendEmailService
from usage_reports.services.openstack_service import (
    OpenStackService,
    OpenStackServiceImpl,
)


LOGGER = logging.getLogger(__name__)


def run_report(config: AppConfig, period: ReportPeriod, force: bool = False) -> int:
    """Default entry point: instantiate real services and run."""
    cloudkitty = CloudKittyServiceImpl(config)
    openstack = OpenStackServiceImpl(config)
    email = ResendEmailService(config)
    return run(
        config=config,
        period=period,
        force=force,
        cloudkitty=cloudkitty,
        openstack=openstack,
        email=email,
    )


def run(
    *,
    config: AppConfig,
    period: ReportPeriod,
    force: bool,
    cloudkitty: CloudKittyService,
    openstack: OpenStackService,
    email: EmailService,
) -> int:
    """Run the report flow with injected services (for tests)."""
    if not force and not _data_is_fresh(cloudkitty, period):
        LOGGER.error(
            "CloudKitty has not finished processing %s; aborting. Pass --force to override.",
            period.label,
        )
        return 2

    projects = cloudkitty.get_summary(period)
    if not projects:
        LOGGER.info("No billable usage found for %s", period.label)
        return 0

    manifest = _load_manifest(config.delivery_manifest_path)
    failed_projects = 0

    for project in projects:
        try:
            project_failed = _process_project(
                config=config,
                period=period,
                project=project,
                manifest=manifest,
                force=force,
                openstack=openstack,
                email=email,
            )
        except Exception as exc:
            failed_projects += 1
            LOGGER.error(
                "Project failed project_id=%s err=%s; continuing",
                project.project_id,
                exc,
            )
            continue

        if project_failed:
            failed_projects += 1

    return 1 if failed_projects > 0 else 0


def _data_is_fresh(cloudkitty: CloudKittyService, period: ReportPeriod) -> bool:
    try:
        scopes = cloudkitty.get_scope_last_processed()
    except Exception as exc:
        LOGGER.warning("Could not check CloudKitty freshness: %s", exc)
        return False
    if not scopes:
        return False
    # Every project scope must be processed past the period end. CloudKitty
    # processes scopes asynchronously; if even one is lagging, the report
    # for that project would under-bill the user, so we wait for the slowest.
    for scope_id, ts in scopes.items():
        if ts is None or ts < period.end_utc:
            LOGGER.info(
                "Scope %s is lagging (last_processed=%s, period_end=%s)",
                scope_id,
                ts,
                period.end_utc,
            )
            return False
    return True


def _process_project(
    *,
    config: AppConfig,
    period: ReportPeriod,
    project: ProjectUsage,
    manifest: dict[str, str],
    force: bool,
    openstack: OpenStackService,
    email: EmailService,
) -> bool:
    """Send the report for one project. Returns True if delivery failed
    for at least one recipient (the caller treats this as a project-level
    failure for exit-code purposes)."""
    project_name = openstack.get_project_name(project.project_id)
    enriched_project = _enrich_project(project, project_name, openstack)

    if enriched_project.total_cost == 0:
        LOGGER.info(
            "Project %s has zero rated usage; skipping email", enriched_project.project_id
        )
        return False

    members = openstack.list_project_members(project.project_id)
    if not members:
        LOGGER.info(
            "Project %s has no members with email; skipping",
            enriched_project.project_id,
        )
        return False

    report = ReportData(period=period, project=enriched_project)
    delivered = 0
    failed = 0
    eligible = 0
    for member in members:
        key = _manifest_key(period, project.project_id, member.email)
        if not force and key in manifest:
            LOGGER.info(
                "Already delivered key=%s on %s; skipping (use --force to resend)",
                key,
                manifest[key],
            )
            continue
        eligible += 1
        try:
            email.send_cost_report(report, member)
        except Exception as exc:
            failed += 1
            LOGGER.error(
                "Email send failed project=%s recipient=%s err=%s",
                project.project_id,
                member.email,
                exc,
            )
            continue
        delivered += 1
        if not config.dry_run:
            manifest[key] = datetime.now(timezone.utc).isoformat()
            # Persist after every successful send so a mid-run crash
            # does not cause the next run to re-deliver this email.
            _save_manifest(config.delivery_manifest_path, manifest)

    LOGGER.info(
        "Project complete project=%s eligible=%s delivered=%s failed=%s total_cost=%s",
        project.project_id,
        eligible,
        delivered,
        failed,
        enriched_project.total_cost,
    )
    # Any recipient failure on this project counts as a project failure
    # so the cron run exits non-zero. Other recipients still receive
    # their emails because we caught per-recipient exceptions above.
    return failed > 0


def _enrich_project(
    project: ProjectUsage,
    project_name: str,
    openstack: OpenStackService,
) -> ProjectUsage:
    enriched_resources: list[ResourceCost] = []
    for resource in project.resources:
        try:
            enriched_resources.append(openstack.enrich_resource(resource))
        except Exception as exc:
            LOGGER.warning(
                "Resource enrichment failed uuid=%s err=%s; using original",
                resource.resource_id,
                exc,
            )
            enriched_resources.append(resource)
    return ProjectUsage(
        project_id=project.project_id,
        project_name=project_name,
        resources=tuple(enriched_resources),
    )


def _manifest_key(period: ReportPeriod, project_id: str, email_addr: str) -> str:
    return f"{period.label}/{project_id}/{email_addr}"


def _load_manifest(path: str) -> dict[str, str]:
    """Load the delivery manifest, refusing to silently treat a corrupt
    file as 'no deliveries yet' -- that would cause duplicate sends."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Delivery manifest at {path} is corrupt ({exc}); refusing to "
            "continue. Inspect/repair the file manually -- treating a corrupt "
            "manifest as empty would re-email every recipient."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Cannot read delivery manifest at {path}: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Delivery manifest at {path} is not a JSON object; refusing to continue."
        )
    return {str(k): str(v) for k, v in raw.items()}


def _save_manifest(path: str, manifest: dict[str, str]) -> None:
    """Write the manifest atomically (tmp file + rename) so a crash mid
    write cannot leave a half-written JSON behind.

    Raises on write failure: silently swallowing here would let the
    caller believe the send is durably recorded when it isn't, causing
    a duplicate email on the next run.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".manifest-", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, p)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

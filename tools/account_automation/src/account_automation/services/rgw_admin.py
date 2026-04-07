"""Ceph RadosGW admin REST API client.

The RGW admin API uses AWS Signature V4 auth (S3 access/secret keys),
not Keystone tokens.  This allows listing and deleting buckets for any
tenant regardless of which Keystone project the caller is scoped to.

Implicit tenants: when ``rgw_keystone_implicit_tenants=true``, RGW creates
one account per Keystone project with UID format ``<project_id>$<project_id>``
(project ID used as both tenant and user).  A UID whose left half is absent
from Keystone belongs to a deleted (orphaned) project.

Required admin credentials: an S3 key-pair with at minimum
``buckets=*;users=*`` capabilities.  Create via::

    radosgw-admin caps add --uid=<admin-uid> --caps="buckets=*;users=*"
    radosgw-admin key create --uid=<admin-uid> --key-type=s3
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests
from requests_aws4auth import AWS4Auth


LOGGER = logging.getLogger(__name__)

# Matches UIDs created by implicit-tenant mode: <id>$<id> where both halves
# are the same Keystone project ID (32-char hex or hyphenated UUID).
_IMPLICIT_UID_RE = re.compile(
    r"^([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\$\1$"
)


@dataclass(frozen=True)
class RgwBucket:
    name: str
    tenant: str
    num_objects: int
    size_bytes: int


class RgwAdminClient:
    """Thin wrapper around the Ceph RadosGW admin REST API."""

    def __init__(self, admin_url: str, access_key: str, secret_key: str, region: str = "") -> None:
        self._base = admin_url.rstrip("/")
        if not self._base.startswith("https://"):
            LOGGER.warning(
                "RGW admin URL %s is not HTTPS; credentials and bucket names will transit unencrypted",
                self._base,
            )
        self._session = requests.Session()
        # requests_aws4auth signs the Host header value it sees during request
        # preparation. Keep it aligned with the actual request netloc, including
        # any explicit port, so RGW validates the signature correctly.
        self._session.headers["Host"] = urlsplit(self._base).netloc
        self._session.auth = AWS4Auth(access_key, secret_key, region, "s3")

    def _get(self, path: str, **params: str) -> object:
        url = f"{self._base}/admin/{path}"
        resp = self._session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _delete_idempotent(self, path: str, noun: str, name: str, **params: str) -> None:
        """DELETE with 404 treated as success (resource already absent)."""
        resp = self._session.delete(f"{self._base}/admin/{path}", params=params)
        if resp.status_code == 404:
            LOGGER.debug("RGW %s %s already absent, nothing to delete", noun, name)
            return
        resp.raise_for_status()

    def list_user_buckets(self, project_id: str) -> list[RgwBucket]:
        """Return buckets owned by the implicit-tenant user for *project_id*.

        Returns an empty list when the user does not exist (HTTP 404) or
        has no buckets.  Raises on other errors so callers can distinguish
        "no buckets" from "listing failed".
        """
        uid = f"{project_id}${project_id}"
        try:
            data = self._get("bucket", uid=uid, stats="true")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []
            raise

        if not isinstance(data, list):
            return []

        result = []
        for entry in data:
            name = entry.get("bucket", "")
            if not name:
                continue
            usage = entry.get("usage", {}).get("rgw.main", {})
            result.append(RgwBucket(
                name=name,
                tenant=project_id,
                num_objects=usage.get("num_objects", 0),
                size_bytes=usage.get("size_actual", 0),
            ))
        return result

    def delete_bucket(self, bucket_name: str, *, tenant: str = "") -> None:
        """Delete *bucket_name* and purge all its objects.

        When *tenant* is provided, the admin API resolves the bucket under
        that tenant namespace (required for implicit-tenant deployments).
        A 404 is treated as success (bucket already gone).
        Raises on other failures; callers should catch and log.
        """
        params: dict[str, str] = {"bucket": bucket_name, "purge-objects": "true"}
        if tenant:
            params["tenant"] = tenant
        self._delete_idempotent("bucket", "bucket", bucket_name, **params)

    def delete_user(self, uid: str) -> None:
        """Delete RGW user *uid*.

        A 404 is treated as success (user already absent).
        """
        self._delete_idempotent("user", "user", uid, uid=uid)

    def delete_implicit_tenant_user(self, project_id: str) -> None:
        """Delete the implicit-tenant RGW user for *project_id*."""
        self.delete_user(f"{project_id}${project_id}")

    def list_implicit_tenant_uids(self) -> list[str]:
        """Return all RGW user UIDs matching the implicit-tenant pattern ``<id>$<id>``.

        These correspond to Keystone projects that had object-storage usage.
        UIDs whose left half is absent from the live Keystone project set
        belong to deleted (orphaned) projects.
        """
        try:
            data: list[str] = []
            marker = ""
            while True:
                params = {"list": "true"}
                if marker:
                    params["marker"] = marker
                page = self._get("user", **params)
                if not isinstance(page, dict):
                    return []
                keys = page.get("keys", [])
                if not isinstance(keys, list):
                    return []
                data.extend(uid for uid in keys if isinstance(uid, str))
                if not page.get("truncated"):
                    break
                next_marker = page.get("marker", "")
                if not isinstance(next_marker, str) or not next_marker or next_marker == marker:
                    break
                marker = next_marker
        except Exception as exc:
            LOGGER.warning("Failed to list RGW user UIDs: %s", exc)
            return []
        return [uid for uid in data if _IMPLICIT_UID_RE.match(uid)]

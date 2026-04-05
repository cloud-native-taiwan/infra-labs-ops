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
    num_objects: int
    size_bytes: int


class RgwAdminClient:
    """Thin wrapper around the Ceph RadosGW admin REST API."""

    def __init__(self, admin_url: str, access_key: str, secret_key: str) -> None:
        self._base = admin_url.rstrip("/")
        if not self._base.startswith("https://"):
            LOGGER.warning(
                "RGW admin URL %s is not HTTPS; credentials and bucket names will transit unencrypted",
                self._base,
            )
        self._session = requests.Session()
        self._session.auth = AWS4Auth(access_key, secret_key, "", "s3")

    def _get(self, path: str, **params: str) -> object:
        url = f"{self._base}/admin/{path}"
        resp = self._session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, **params: str) -> None:
        url = f"{self._base}/admin/{path}"
        resp = self._session.delete(url, params=params)
        resp.raise_for_status()

    def list_user_buckets(self, project_id: str) -> list[RgwBucket]:
        """Return buckets owned by the implicit-tenant user for *project_id*.

        Returns an empty list when the user does not exist (HTTP 404) or
        has no buckets.
        """
        uid = f"{project_id}${project_id}"
        try:
            data = self._get("bucket", uid=uid, stats="true")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []
            LOGGER.warning("Failed to list RGW buckets for project %s: %s", project_id, exc)
            return []
        except Exception as exc:
            LOGGER.warning("Failed to list RGW buckets for project %s: %s", project_id, exc)
            return []

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
                num_objects=usage.get("num_objects", 0),
                size_bytes=usage.get("size_actual", 0),
            ))
        return result

    def delete_bucket(self, bucket_name: str) -> None:
        """Delete *bucket_name* and purge all its objects.

        Raises on failure; callers should catch and log.
        """
        self._delete("bucket", bucket=bucket_name, **{"purge-objects": "true"})

    def list_implicit_tenant_uids(self) -> list[str]:
        """Return all RGW user UIDs matching the implicit-tenant pattern ``<id>$<id>``.

        These correspond to Keystone projects that had object-storage usage.
        UIDs whose left half is absent from the live Keystone project set
        belong to deleted (orphaned) projects.
        """
        try:
            data = self._get("metadata/user")
        except Exception as exc:
            LOGGER.warning("Failed to list RGW user UIDs: %s", exc)
            return []
        if not isinstance(data, list):
            return []
        return [uid for uid in data if isinstance(uid, str) and _IMPLICIT_UID_RE.match(uid)]

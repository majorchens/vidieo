"""Read-only access to old AI Centre assets through its own authorization.

The registry stores references, not OSS objects or credentials. An explicit
identity mapping and Work OS project permission are required before a private
asset is offered to an employee. The old service then checks its own session
and issues a temporary OSS URL; Work OS never signs an OSS request itself.
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
from http import cookies
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import asset_center
import store

OLD_SESSION_COOKIE = "wujing_studio_session"
REGISTRY_ID = re.compile(r"uar_[a-f0-9]{16}\Z")
SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
ALLOWED_MEDIA = {"image", "video", "audio", "document"}


def initialize() -> None:
    """Create only the manager-reviewed identity map; never mutate old data."""
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS legacy_asset_identity_map(
            legacy_username TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id),
            work_user_id TEXT NOT NULL REFERENCES users(id),
            active INTEGER NOT NULL DEFAULT 1,
            verified_by TEXT NOT NULL REFERENCES users(id),
            verified_at TEXT NOT NULL,
            PRIMARY KEY(legacy_username, project_id)
        );
        CREATE INDEX IF NOT EXISTS idx_legacy_identity_work_user
            ON legacy_asset_identity_map(work_user_id, project_id, active);
        """)


def map_identity(actor: dict, legacy_username: str, work_user_id: str,
                 project_id: str, *, active: bool = True) -> dict:
    """Record an explicit admin verification; matching usernames alone are insufficient."""
    if actor.get("role") not in {"manager", "founder"}:
        raise PermissionError("仅管理员可确认旧账号映射")
    legacy_username = str(legacy_username or "").strip()
    if not legacy_username or len(legacy_username) > 80 or any(ord(ch) < 32 for ch in legacy_username):
        raise ValueError("旧账号名称无效")
    initialize()
    with store.connect() as c:
        if not c.execute("SELECT 1 FROM users WHERE id=? AND active=1", (work_user_id,)).fetchone():
            raise ValueError("Work OS 用户不存在或已停用")
        if not c.execute("SELECT 1 FROM projects WHERE id=? AND active=1", (project_id,)).fetchone():
            raise ValueError("项目不存在或已停用")
        c.execute("""INSERT INTO legacy_asset_identity_map
                     (legacy_username,project_id,work_user_id,active,verified_by,verified_at)
                     VALUES(?,?,?,?,?,?)
                     ON CONFLICT(legacy_username,project_id) DO UPDATE SET
                       work_user_id=excluded.work_user_id,active=excluded.active,
                       verified_by=excluded.verified_by,verified_at=excluded.verified_at""",
                  (legacy_username, project_id, work_user_id, int(active), actor["id"], store.now()))
        store.audit(c, actor["id"], "legacy_asset.identity_map", None,
                    {"legacy_username": legacy_username, "project_id": project_id,
                     "work_user_id": work_user_id, "active": bool(active)})
    return {"legacy_username": legacy_username, "project_id": project_id,
            "work_user_id": work_user_id, "active": bool(active)}


def _registry_row(c, asset_id: str) -> dict:
    if not REGISTRY_ID.fullmatch(str(asset_id or "")):
        raise ValueError("素材编号无效")
    row = c.execute("SELECT * FROM asset_registry WHERE asset_id=?", (asset_id,)).fetchone()
    if not row:
        raise KeyError(asset_id)
    item = dict(row)
    if not item["source_system"].startswith("legacy_ai"):
        raise ValueError("此素材不是旧 AI 中心资产")
    return item


def can_access(c, row: dict, user: dict) -> bool:
    """Work OS authorization. The source service performs a second check."""
    if not row["source_system"].startswith("legacy_ai"):
        return False
    if user.get("role") in {"manager", "founder"}:
        return True
    if user.get("role") != "employee" or not row.get("project_id"):
        return False
    if not asset_center._project_allowed(c, user, row["project_id"]):
        return False
    meta = store.parse(row.get("metadata"), {}) or {}
    if meta.get("visibility") == "team":
        return True
    if meta.get("visibility") != "private" or not meta.get("owner_username"):
        return False
    try:
        mapping = c.execute("""SELECT work_user_id FROM legacy_asset_identity_map
                               WHERE legacy_username=? AND project_id=? AND active=1""",
                            (meta["owner_username"], row["project_id"])).fetchone()
    except sqlite3.OperationalError:
        # Before migration, or if mapping cannot be read, fail closed.
        return False
    return bool(mapping and secrets.compare_digest(str(mapping[0]), str(user.get("id") or "")))


def _source_asset_id(c, row: dict) -> str | None:
    source = c.execute("""SELECT original_id FROM asset_registry_sources
                          WHERE asset_id=? AND source_system='legacy_ai_center' LIMIT 1""",
                       (row["asset_id"],)).fetchone()
    if not source:
        return None
    original = str(source[0])
    return original if SOURCE_ID.fullmatch(original) else None


def descriptor(user: dict, asset_id: str) -> dict:
    """Safe browser metadata; no private object URL or token is returned."""
    with store.connect() as c:
        row = _registry_row(c, asset_id)
        if not can_access(c, row, user):
            raise PermissionError("没有此素材的访问权限")
        source_id = _source_asset_id(c, row)
    if row["status"] not in {"active", "completed", "succeeded"}:
        source_id = None
    previewable = bool(source_id and row["type"] in ALLOWED_MEDIA)
    return {"asset_id": asset_id, "project_id": row["project_id"],
            "type": row["type"], "name": row["name"],
            "preview_url": f"/api/asset-center/{asset_id}/legacy-preview" if previewable else None,
            "can_select": previewable, "requires_old_login": previewable,
            "source_status": "ready" if previewable else "source_unavailable"}


def _source_origin() -> str:
    origin = os.environ.get("YOODUN_LEGACY_SOURCE_ORIGIN", "https://ai.duodianqian.cn").rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("旧 AI 中心地址配置无效")
    return origin


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _source_redirect(origin: str, source_id: str, session: str) -> str:
    endpoint = origin + "/api/assets/" + quote(source_id, safe="") + "/content"
    request = Request(endpoint, headers={"Cookie": OLD_SESSION_COOKIE + "=" + session,
                                         "Accept": "*/*"}, method="GET")
    try:
        response = build_opener(_NoRedirect()).open(request, timeout=8)
    except HTTPError as exc:
        if exc.code in {301, 302, 303, 307, 308}:
            location = exc.headers.get("Location", "")
            if not location:
                raise ValueError("旧素材访问链接缺失") from None
            return location
        if exc.code in {401, 403, 404}:
            raise PermissionError("旧 AI 中心登录失效或无此素材权限") from None
        raise ValueError("旧 AI 中心暂时无法读取素材") from None
    except (URLError, TimeoutError):
        raise ValueError("旧 AI 中心暂时无法连接") from None
    else:
        response.close()
        raise ValueError("旧 AI 中心未返回临时访问链接")


def _signed_url_allowed(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    extra = {value.strip().lower() for value in
             os.environ.get("YOODUN_LEGACY_ALLOWED_OSS_HOSTS", "").split(",") if value.strip()}
    allowed = bool(re.fullmatch(r"(?:[a-z0-9-]+\.)?oss-[a-z0-9-]+\.aliyuncs\.com", host)) or host in extra
    return bool(parsed.scheme == "https" and host and allowed and not parsed.username
                and not parsed.password and not parsed.fragment and parsed.query)


def resolve_signed_access(user: dict, asset_id: str, browser_cookie_header: str) -> str:
    """Return an ephemeral URL for one already-authorized preview/generation.

    The caller must not save, log, or expose this URL in JSON. A preview route
    may issue a no-store 307. A generator may use it only during this request.
    """
    with store.connect() as c:
        row = _registry_row(c, asset_id)
        if not can_access(c, row, user):
            raise PermissionError("没有此素材的访问权限")
        source_id = _source_asset_id(c, row)
    if not source_id or row["type"] not in ALLOWED_MEDIA or row["status"] not in {"active", "completed", "succeeded"}:
        raise ValueError("旧素材暂不可预览")
    try:
        parsed_cookie = cookies.SimpleCookie()
        parsed_cookie.load(browser_cookie_header or "")
        session = parsed_cookie[OLD_SESSION_COOKIE].value if OLD_SESSION_COOKIE in parsed_cookie else ""
    except cookies.CookieError:
        session = ""
    if not session:
        raise PermissionError("请先登录旧 AI 中心")
    url = _source_redirect(_source_origin(), source_id, session)
    if not _signed_url_allowed(url):
        raise ValueError("旧 AI 中心返回了不受信任的素材地址")
    return url


def creation_reference(user: dict, asset_id: str, project_id: str,
                       browser_cookie_header: str) -> dict:
    """Resolve a selected old asset for an in-request model reference.

    Do not persist the returned signed URL. The model adapter must support this
    media type before a generation is submitted.
    """
    info = descriptor(user, asset_id)
    if not info["can_select"] or info["project_id"] != project_id:
        raise PermissionError("旧素材与当前项目不匹配")
    signed_url = resolve_signed_access(user, asset_id, browser_cookie_header)
    return {"asset_registry_id": asset_id, "project_id": project_id,
            "type": info["type"], "name": info["name"], "temporary_url": signed_url}

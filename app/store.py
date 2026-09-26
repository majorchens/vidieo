"""Work OS records. Existing project files are referenced, never rewritten."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("YOODUN_DATA_DIR", ROOT / "data"))
DB = DATA / "work_os.sqlite3"
PROJECT_ROOTS = {
    "wuxiang": Path("/Users/majorchen/Documents/ChatGPT/万象武境编剧"),
    "diaojianghu": Path("/Users/majorchen/Documents/ChatGPT/钓江湖"),
    "dingting": Path("/Users/majorchen/Documents/ChatGPT/助听器"),
}
SHARED_ROOT = Path("/Users/majorchen/Documents/ChatGPT/武术短剧拍摄")
AGENT_ROOT = SHARED_ROOT / ".codex" / "agents"
ASSET_TYPES = {"image", "video", "script", "prompt", "character", "motion_reference", "audio", "document", "external_url"}
MOTION_FILE_LIMIT = 512_000_000  # Raw-binary martial upload; independent of JSON/base64 limits.
STATES = {"planned", "ready", "assigned", "in_progress", "submitted", "technical_checked", "ai_prechecked", "human_review", "accepted", "revision_required", "blocked", "failed", "unknown_submission"}
ROLES = {"founder", "manager", "employee"}
KNOWLEDGE_STATES = {"candidate", "active", "superseded", "revoked", "historical"}
FEEDBACK_KINDS = {"task", "workflow", "capability", "project_style"}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def parse(value, default=None):
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def production_root() -> Path:
    """Private, non-Git production media on the company server."""
    return DATA / "AI Production Assets"


def asset_roots(project_id: str) -> tuple[Path, ...]:
    return ((DATA / "uploads" / project_id).resolve(),
            (DATA / "inputs" / project_id).resolve(),
            (production_root() / project_id).resolve())


def valid_asset_path(project_id: str, raw: str) -> Path:
    if project_id not in PROJECT_ROOTS:
        raise ValueError("未知项目")
    path = Path(raw).expanduser().resolve(strict=True)
    roots = [PROJECT_ROOTS[project_id].resolve(), SHARED_ROOT.resolve(), *asset_roots(project_id)]
    if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
        raise ValueError("资产必须是该项目或共享制作组中的现有文件")
    secret_name = re.search(r"(?:^|[._-])(auth|token|secret|credential|password|api[-_]?key)(?:[._-]|$)", path.name, re.I)
    if any(part in {".env", "state", "secrets", "credentials", ".git"} for part in path.parts) or secret_name:
        raise ValueError("资产路径不允许登记")
    if path.suffix.lower() not in {".md", ".txt", ".json", ".srt", ".png", ".jpg", ".jpeg", ".webp", ".glb", ".mp4", ".mov", ".wav", ".mp3", ".pdf"}:
        raise ValueError("文件类型不允许登记")
    return path


@contextmanager
def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def record(connection, table: str, item_id: str):
    if table not in {"users", "tasks", "assets", "deliverables", "projects", "workflows"}:
        raise ValueError("无效记录类型")
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if row is None:
        raise KeyError(item_id)
    return dict(row)


def audit(connection, actor: str, action: str, task_id: str | None, data: dict | None = None):
    connection.execute("INSERT INTO audit(at,actor,action,task_id,data) VALUES(?,?,?,?,?)", (now(), actor, action, task_id, dumps(data or {})))


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        version = c.execute("SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
        if version < 1:
            c.executescript("""
            CREATE TABLE users(id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
            CREATE TABLE sessions(token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
            CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, source_root TEXT NOT NULL, workflow_project TEXT, project_override TEXT NOT NULL DEFAULT '{}', active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE workflows(id TEXT PRIMARY KEY, project_id TEXT, name TEXT NOT NULL, description TEXT NOT NULL, version TEXT NOT NULL, input_contract TEXT NOT NULL, qc_contract TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE capabilities(id TEXT PRIMARY KEY, name TEXT NOT NULL, canonical_source TEXT NOT NULL, sha256 TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL);
            CREATE TABLE assets(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), type TEXT NOT NULL, name TEXT NOT NULL, storage_ref TEXT NOT NULL, sha256 TEXT, version INTEGER NOT NULL, source_refs TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL);
            CREATE TABLE tasks(id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), workflow_id TEXT NOT NULL REFERENCES workflows(id), title TEXT NOT NULL, why TEXT NOT NULL, assignee_id TEXT REFERENCES users(id), priority INTEGER NOT NULL DEFAULT 2, due_at TEXT, status TEXT NOT NULL, context TEXT NOT NULL, input_assets TEXT NOT NULL, instructions TEXT NOT NULL, ai_prepared TEXT, deliverable_contract TEXT NOT NULL, qc_contract TEXT NOT NULL, budget_cap REAL NOT NULL DEFAULT 0, execution_job_refs TEXT NOT NULL DEFAULT '[]', feedback_refs TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 0, character_lock TEXT, motion_lock TEXT, founder_required INTEGER NOT NULL DEFAULT 0, blocked_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, created_by TEXT NOT NULL);
            CREATE TABLE deliverables(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), version INTEGER NOT NULL, asset_id TEXT REFERENCES assets(id), external_url TEXT, provider_job_id TEXT, note TEXT NOT NULL, exception_note TEXT, submitted_by TEXT NOT NULL REFERENCES users(id), submitted_at TEXT NOT NULL, UNIQUE(task_id,version));
            CREATE TABLE qc(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), deliverable_id TEXT NOT NULL REFERENCES deliverables(id), stage TEXT NOT NULL, result TEXT NOT NULL, criteria TEXT NOT NULL, findings TEXT NOT NULL, reviewer_id TEXT, method TEXT NOT NULL, coverage TEXT NOT NULL, evidence_refs TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE feedback(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), deliverable_id TEXT NOT NULL REFERENCES deliverables(id), version INTEGER NOT NULL, kind TEXT NOT NULL, problem TEXT NOT NULL, change_request TEXT NOT NULL, preserve TEXT NOT NULL, author_id TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE TABLE candidate_learning(id TEXT PRIMARY KEY, feedback_id TEXT NOT NULL REFERENCES feedback(id), task_id TEXT NOT NULL REFERENCES tasks(id), scope TEXT NOT NULL, conditions TEXT NOT NULL, counterexample TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE TABLE knowledge(id TEXT PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL, effective_at TEXT, supersedes TEXT, owner TEXT NOT NULL, content_ref TEXT NOT NULL);
            CREATE TABLE ai_jobs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), kind TEXT NOT NULL, plan_id TEXT, final_job_id TEXT, request_key TEXT NOT NULL UNIQUE, status TEXT NOT NULL, error TEXT, usage TEXT, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE costs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), provider TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL, usage TEXT, reserved_cost REAL, actual_cost REAL, provider_job_id TEXT, created_at TEXT NOT NULL);
            CREATE TABLE audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, task_id TEXT, data TEXT NOT NULL);
            CREATE INDEX idx_tasks_assignee ON tasks(assignee_id,status,due_at);
            CREATE INDEX idx_assets_project ON assets(project_id,type);
            CREATE INDEX idx_deliverables_task ON deliverables(task_id,version);
            CREATE INDEX idx_ai_jobs_task ON ai_jobs(task_id,kind);
            """)
            c.execute("INSERT INTO schema_version(version,applied_at) VALUES(1,?)", (now(),))
        if version < 2:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS pilot_observations(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), employee_id TEXT NOT NULL REFERENCES users(id), first_understood INTEGER, knew_next_step INTEGER, materials_clear INTEGER, ai_prepared_clear INTEGER, submitted_success INTEGER, revision_clear INTEGER, duration_minutes REAL, founder_interventions INTEGER, extra_explanations INTEGER, blockers TEXT NOT NULL, recorded_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_pilot_task ON pilot_observations(task_id);
            """)
            c.execute("INSERT INTO schema_version(version,applied_at) VALUES(2,?)", (now(),))
        if version < 3:
            c.executescript("""
            CREATE TABLE task_candidates(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), asset_id TEXT REFERENCES assets(id), external_url TEXT, provider_job_id TEXT, label TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', selected INTEGER NOT NULL DEFAULT 0, choice_reason TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL);
            CREATE INDEX idx_candidates_task ON task_candidates(task_id,created_at);
            CREATE TABLE copilot_messages(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), user_id TEXT NOT NULL REFERENCES users(id), category TEXT NOT NULL, question TEXT NOT NULL, answer TEXT, next_action TEXT, needs_manager INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, ai_job_id TEXT REFERENCES ai_jobs(id), created_at TEXT NOT NULL, completed_at TEXT);
            CREATE INDEX idx_copilot_task ON copilot_messages(task_id,created_at);
            CREATE TABLE escalations(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), message_id TEXT REFERENCES copilot_messages(id), reason TEXT NOT NULL, status TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, resolved_by TEXT REFERENCES users(id), resolution TEXT, resolved_at TEXT);
            CREATE INDEX idx_escalations_status ON escalations(status,created_at);
            """)
            c.execute("INSERT INTO schema_version(version,applied_at) VALUES(3,?)", (now(),))
        c.execute("INSERT OR IGNORE INTO users(id,username,display_name,role,password_hash,active,created_at) VALUES(?,?,?,?,?,?,?)",("u_system","_system","系统检查","manager","disabled",0,now()))
        for pid, name, wf_project in [
            ("wuxiang", "万象武境", "万象武境编剧"),
            ("diaojianghu", "钓江湖", "钓江湖"),
            ("dingting", "顶听", "助听器"),
        ]:
            c.execute("INSERT OR IGNORE INTO projects(id,name,source_root,workflow_project) VALUES(?,?,?,?)", (pid, name, str(PROJECT_ROOTS[pid]), wf_project))
        workflows = [
            ("WF-01", "wuxiang", "功法教学资产制作", "一招的真实动作参考、角色锁定、AI准备、媒体执行与四层QC"),
            ("WF-02", "wuxiang", "创意短视频制作", "Film Studio创意、台本、分镜、生成、剪辑、审片与发布包"),
            ("WF-03", "diaojianghu", "钓江湖内容与运营", "内容或渠道任务的准备、执行、提交、复盘与验收"),
        ]
        for wid, pid, name, description in workflows:
            c.execute("INSERT OR IGNORE INTO workflows(id,project_id,name,description,version,input_contract,qc_contract) VALUES(?,?,?,?,?,?,?)", (wid, pid, name, description, "0.1", "{}", "{}"))
        registry_path = ROOT / "registry" / "canonical_agents.json"
        if registry_path.is_file():
            for agent in json.loads(registry_path.read_text()):
                c.execute("INSERT OR IGNORE INTO capabilities(id,name,canonical_source,sha256,version,status) VALUES(?,?,?,?,?,?)", (agent["id"],agent["name"],agent["canonical_source"],agent["sha256"],agent["version"],"active"))


def password_hash(password: str) -> str:
    if len(password) < 12:
        raise ValueError("密码至少 12 位")
    salt = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260000)
    return "pbkdf2_sha256$260000$" + salt.hex() + "$" + key.hex()


def password_valid(password: str, encoded: str) -> bool:
    try:
        kind, n, salt, key = encoded.split("$")
        if kind != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(n))
        return secrets.compare_digest(actual, bytes.fromhex(key))
    except (ValueError, TypeError):
        return False


def create_user(username: str, display_name: str, role: str, password: str, actor: str = "system") -> str:
    if role not in ROLES or not username or len(username) > 80 or not display_name or len(display_name) > 100:
        raise ValueError("用户资料无效")
    uid = "u_" + secrets.token_hex(8)
    with connect() as c:
        c.execute("INSERT INTO users(id,username,display_name,role,password_hash,created_at) VALUES(?,?,?,?,?,?)", (uid, username, display_name, role, password_hash(password), now()))
        audit(c, actor, "user.create", None, {"user_id": uid, "role": role})
    return uid


def authenticate(username: str, password: str):
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if row is None or not password_valid(password, row["password_hash"]):
            return None
        token = secrets.token_urlsafe(40)
        csrf = secrets.token_urlsafe(24)
        expiry = datetime.fromtimestamp(datetime.now().timestamp() + 12 * 3600, timezone.utc).isoformat()
        c.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires_at) VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), row["id"], csrf, expiry))
        return token, csrf, {k: row[k] for k in ("id", "username", "display_name", "role")}


def session_user(token: str):
    if not token:
        return None
    with connect() as c:
        row = c.execute("SELECT u.id,u.username,u.display_name,u.role,s.csrf,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND u.active=1", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None or row["expires_at"] < datetime.now(timezone.utc).isoformat():
            return None
        return dict(row)


def logout(token: str):
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def project_allowed(user: dict, project_id: str) -> bool:
    if user["role"] in {"founder", "manager"}:
        return True
    with connect() as c:
        return c.execute("SELECT 1 FROM tasks WHERE project_id=? AND assignee_id=? LIMIT 1", (project_id, user["id"])).fetchone() is not None


def register_asset(project_id: str, asset_type: str, name: str, storage_ref: str, actor: str, source_refs=None, status="active", source_sha256=None) -> dict:
    if asset_type not in ASSET_TYPES or not name or len(name) > 180:
        raise ValueError("资产类型或名称无效")
    if project_id not in PROJECT_ROOTS:
        raise ValueError("未知项目")
    if storage_ref.startswith("https://") or storage_ref.startswith("studio://"):
        if len(storage_ref)>2000 or (not storage_ref.startswith("https://") and not re.fullmatch(r"studio://[A-Za-z0-9._-]{4,160}",storage_ref)):
            raise ValueError("外部素材引用无效")
        if asset_type != "external_url" and not re.fullmatch(r"[a-f0-9]{64}",str(source_sha256 or "")):
            raise ValueError("远程素材需要已核实的 SHA-256")
        sha = source_sha256 if asset_type != "external_url" else None
    else:
        if asset_type == "external_url":
            raise ValueError("外部资产需要 HTTPS 地址")
        path = valid_asset_path(project_id, storage_ref)
        storage_ref = str(path)
        sha = digest_file(path)
    aid = "a_" + secrets.token_hex(8)
    with connect() as c:
        version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM assets WHERE project_id=? AND name=?", (project_id, name)).fetchone()[0]
        c.execute("INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (aid, project_id, asset_type, name, storage_ref, sha, version, dumps(source_refs or []), status, now(), actor))
        audit(c, actor, "asset.register", None, {"asset_id": aid, "project_id": project_id, "version": version})
        return record(c, "assets", aid)


def register_submission_asset(project_id: str, filename: str, contents: bytes, actor: str) -> dict:
    if project_id not in PROJECT_ROOTS or not contents or len(contents) > 50_000_000:
        raise ValueError("提交文件为空或超过 50MB")
    suffix = Path(filename).suffix.lower()
    types = {".png":"image",".jpg":"image",".jpeg":"image",".webp":"image",".glb":"character",".mp4":"video",".mov":"video",".wav":"audio",".mp3":"audio",".md":"document",".txt":"document",".pdf":"document",".json":"document"}
    if suffix not in types:
        raise ValueError("不支持此提交文件类型")
    safe_name = Path(filename).name[:160]
    if re.search(r"(?:^|[._-])(auth|token|secret|credential|password|api[-_]?key|env)(?:[._-]|$)",safe_name,re.I):
        raise ValueError("疑似凭据文件，禁止提交")
    sha = hashlib.sha256(contents).hexdigest()
    aid = "a_" + secrets.token_hex(8)
    dest_dir = DATA / "uploads" / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (aid + suffix)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(dest, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(contents)
            f.flush()
            os.fsync(f.fileno())
        with connect() as c:
            c.execute("INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (aid,project_id,types[suffix],safe_name,str(dest),sha,1,"[]","active",now(),actor))
            audit(c,actor,"asset.upload",None,{"asset_id":aid,"project_id":project_id})
            return record(c,"assets",aid)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def register_production_asset(project_id: str, role: str, filename: str, contents: bytes, actor: str) -> dict:
    """Store a new lesson asset by role; never overwrite a prior version."""
    if project_id != "wuxiang" or not re.fullmatch(r"[a-z_]{2,40}", role):
        raise ValueError("生产资产项目或用途无效")
    if not contents or len(contents) > 50_000_000:
        raise ValueError("生产资产为空或超过 50MB")
    suffix = Path(filename).suffix.lower()
    types = {".png":"image", ".jpg":"image", ".jpeg":"image", ".webp":"image", ".glb":"character",
             ".mp4":"video", ".mov":"video", ".wav":"audio", ".mp3":"audio",
             ".md":"document", ".txt":"document", ".json":"document", ".srt":"document"}
    if suffix not in types:raise ValueError("生产资产文件类型无效")
    safe_name = Path(filename).name[:160]
    if not safe_name or re.search(r"(?:^|[._-])(auth|token|secret|credential|password|api[-_]?key|env)(?:[._-]|$)",safe_name,re.I):
        raise ValueError("生产资产文件名无效")
    aid = "a_" + secrets.token_hex(8)
    dest_dir = production_root() / project_id / role
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (aid + suffix)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd,"wb") as output:
            output.write(contents);output.flush();os.fsync(output.fileno())
        sha = hashlib.sha256(contents).hexdigest()
        with connect() as c:
            version = c.execute("SELECT COALESCE(MAX(version),0)+1 FROM assets WHERE project_id=? AND name=?",(project_id,safe_name)).fetchone()[0]
            c.execute("INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (aid,project_id,types[suffix],safe_name,str(dest),sha,version,"[]","active",now(),actor))
            audit(c,actor,"lesson.asset.upload",None,{"asset_id":aid,"role":role,"version":version})
            return record(c,"assets",aid)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def register_motion_file(filename: str, staged: Path, actor: str, expected_sha256: str | None = None) -> dict:
    """Atomically promote a streamed MP4/MOV into the private Wuxiang asset store."""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mp4", ".mov"}:
        raise ValueError("真人动作需为 MP4/MOV 视频")
    safe_name = Path(filename).name[:160]
    if not safe_name or re.search(r"(?:^|[._-])(auth|token|secret|credential|password|api[-_]?key|env)(?:[._-]|$)", safe_name, re.I):
        raise ValueError("视频文件名无效")
    dest_dir = production_root() / "wuxiang" / "motion_reference"
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged = Path(staged)
    if staged.is_symlink() or not staged.is_file() or staged.resolve().parent != dest_dir.resolve():
        raise ValueError("视频暂存路径无效")
    size = staged.stat().st_size
    if not 0 < size <= MOTION_FILE_LIMIT:
        raise ValueError("真人动作视频为空或超过 512MB 上限")
    sha = digest_file(staged)
    if expected_sha256 is not None and sha != expected_sha256:
        raise ValueError("视频校验失败，请重新上传")
    aid = "a_" + secrets.token_hex(8)
    dest = dest_dir / (aid + suffix)
    if dest.exists():
        raise RuntimeError("视频保存位置已占用")
    os.replace(staged, dest)
    try:
        with connect() as c:
            c.execute("INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, "wuxiang", "motion_reference", safe_name, str(dest), sha, 1, "[]", "active", now(), actor))
            audit(c, actor, "asset.upload", None, {"asset_id": aid, "project_id": "wuxiang"})
            return record(c, "assets", aid)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def register_candidate_file(job_id: str, staged: Path, sha256: str) -> dict:
    """Register a streamed connector result, retaining a staged retry copy."""
    if not re.fullmatch(r"mj_[a-f0-9]{16}", job_id) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("候选视频标识无效")
    stage_dir = DATA / "connector_staging" / "martial"
    staged = Path(staged)
    if staged.is_symlink() or not staged.is_file() or staged.resolve() != (stage_dir / (job_id + ".mp4")).resolve():
        raise ValueError("候选视频暂存路径无效")
    size = staged.stat().st_size
    if not 0 < size <= MOTION_FILE_LIMIT or digest_file(staged) != sha256:
        raise ValueError("候选视频大小或校验值无效")
    source_refs = dumps(["martial_media_job:" + job_id])
    with connect() as c:
        existing = c.execute("SELECT * FROM assets WHERE project_id='wuxiang' AND created_by='local-connector' AND source_refs=? ORDER BY created_at DESC LIMIT 1",
                             (source_refs,)).fetchone()
        if existing:
            if existing["sha256"] != sha256 or existing["type"] != "video" or not Path(existing["storage_ref"]).is_file():
                raise ValueError("此视频作业已登记不同的生成结果")
            return dict(existing)
    aid = "a_" + secrets.token_hex(8)
    dest_dir = production_root() / "wuxiang" / "candidate_video"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (aid + ".mp4")
    os.link(staged, dest)
    try:
        with connect() as c:
            c.execute("INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, "wuxiang", "video", job_id + ".mp4", str(dest), sha256, 1, source_refs, "active", now(), "local-connector"))
            audit(c, "local-connector", "asset.upload", None, {"asset_id": aid, "project_id": "wuxiang"})
            return record(c, "assets", aid)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def register_ai_video_file(project_id: str, source: Path, actor: str) -> dict:
    """Link a completed AI Studio video into a project's private asset store."""
    if project_id not in PROJECT_ROOTS:
        raise ValueError("未知项目")
    source = Path(source)
    result_dir = (DATA / "ai_studio" / "results").resolve()
    if (source.is_symlink() or not source.is_file() or
            source.resolve().parent != result_dir or
            not re.fullmatch(r"aia_[a-f0-9]{16}\.mp4", source.name)):
        raise ValueError("AI 视频成果路径无效")
    size = source.stat().st_size
    if not 0 < size <= MOTION_FILE_LIMIT:
        raise ValueError("AI 视频成果为空或超过 512MB")
    sha = digest_file(source)
    aid = "a_" + secrets.token_hex(8)
    dest_dir = DATA / "uploads" / project_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (aid + ".mp4")
    os.link(source, dest)
    try:
        with connect() as c:
            c.execute("INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, project_id, "video", source.name, str(dest), sha, 1, "[]", "active", now(), actor))
            audit(c, actor, "asset.upload", None, {"asset_id": aid, "project_id": project_id})
            return record(c, "assets", aid)
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def list_assets(project_id: str):
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM assets WHERE project_id=? ORDER BY created_at DESC LIMIT 200", (project_id,))]


def task_visible(c, task: dict, user: dict) -> bool:
    return user["role"] in {"founder", "manager"} or task["assignee_id"] == user["id"]


def task_for_user(task_id: str, user: dict):
    with connect() as c:
        task = record(c, "tasks", task_id)
        if not task_visible(c, task, user):
            raise PermissionError("没有此任务的访问权限")
        return task


def task_public(task: dict, advanced=False):
    keys = ["id", "project_id", "workflow_id", "title", "why", "assignee_id", "priority", "due_at", "status", "context", "input_assets", "instructions", "ai_prepared", "deliverable_contract", "qc_contract", "budget_cap", "revision", "character_lock", "motion_lock", "founder_required", "blocked_reason", "created_at", "updated_at"]
    out = {k: task[k] for k in keys}
    for key in ("context", "input_assets", "instructions", "ai_prepared", "deliverable_contract", "qc_contract", "character_lock", "motion_lock"):
        out[key] = parse(out[key], {} if key not in {"input_assets", "instructions"} else [])
    if advanced:
        out["execution_job_refs"] = parse(task["execution_job_refs"], [])
        out["feedback_refs"] = parse(task["feedback_refs"], [])
    else:
        out.pop("budget_cap", None)
    return out


def detail(task_id: str, user: dict):
    with connect() as c:
        task = record(c, "tasks", task_id)
        if not task_visible(c, task, user):
            raise PermissionError("没有此任务的访问权限")
        out = task_public(task, user["role"] != "employee")
        out["assets"] = [dict(r) for r in c.execute("SELECT a.* FROM assets a JOIN json_each(?) j ON a.id=j.value WHERE a.project_id=?", (task["input_assets"], task["project_id"]))]
        for asset in out["assets"]:
            asset["remote"]=asset["storage_ref"].startswith(("https://","studio://"))
            asset["extension"]=Path(asset["storage_ref"]).suffix.lower() if not asset["remote"] else ""
        if user["role"] == "employee":
            out["assets"] = [{k:a[k] for k in ("id","project_id","type","name","version","status","created_at","remote","extension")} for a in out["assets"]]
        out["deliverables"] = [dict(r) for r in c.execute("SELECT * FROM deliverables WHERE task_id=? ORDER BY version DESC", (task_id,))]
        out["qc"] = [dict(r) for r in c.execute("SELECT * FROM qc WHERE task_id=? ORDER BY created_at DESC", (task_id,))]
        out["feedback"] = [dict(r) for r in c.execute("SELECT * FROM feedback WHERE task_id=? ORDER BY created_at DESC", (task_id,))]
        out["candidates"] = [dict(r) for r in c.execute("SELECT c.*,a.type asset_type,a.name asset_name FROM task_candidates c LEFT JOIN assets a ON a.id=c.asset_id WHERE c.task_id=? ORDER BY c.created_at DESC", (task_id,))]
        out["copilot_messages"] = [dict(r) for r in c.execute("SELECT id,category,question,answer,next_action,needs_manager,status,created_at,completed_at FROM copilot_messages WHERE task_id=? ORDER BY created_at DESC LIMIT 12", (task_id,))][::-1]
        out["escalations"] = [dict(r) for r in c.execute("SELECT id,reason,status,created_at,resolution,resolved_at FROM escalations WHERE task_id=? ORDER BY created_at DESC LIMIT 10", (task_id,))]
        for row in out["qc"]:
            row["findings"] = parse(row["findings"], [])
            row["criteria"] = parse(row["criteria"], [])
            row["evidence_refs"] = parse(row["evidence_refs"], [])
        return out


def add_candidate(task_id: str, user: dict, data: dict) -> dict:
    label = str(data.get("label") or "").strip()[:120]
    note = str(data.get("note") or "").strip()[:1500]
    asset_id = str(data.get("asset_id") or "") or None
    external_url = str(data.get("external_url") or "").strip() or None
    provider_job_id = str(data.get("provider_job_id") or "").strip()[:200] or None
    if not label or not any((asset_id, external_url, provider_job_id)):
        raise ValueError("候选作品需要名称和文件、链接或平台任务编号")
    if external_url and (not external_url.startswith("https://") or len(external_url)>2000):
        raise ValueError("候选链接必须为 HTTPS")
    with connect() as c:
        task = record(c,"tasks",task_id)
        if task["assignee_id"] != user["id"] or task["status"] != "in_progress":
            raise PermissionError("只有执行中的负责人可添加候选")
        if asset_id and record(c,"assets",asset_id)["project_id"] != task["project_id"]:
            raise ValueError("候选文件不属于当前项目")
        cid="cand_"+secrets.token_hex(8)
        c.execute("INSERT INTO task_candidates(id,task_id,asset_id,external_url,provider_job_id,label,note,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid,task_id,asset_id,external_url,provider_job_id,label,note,user["id"],now()))
        audit(c,user["id"],"candidate.add",task_id,{"candidate_id":cid})
        return dict(c.execute("SELECT * FROM task_candidates WHERE id=?",(cid,)).fetchone())


def choose_candidate(task_id: str, candidate_id: str, reason: str, user: dict) -> dict:
    reason=reason.strip()
    if len(reason)<4 or len(reason)>1200:
        raise ValueError("请简要说明选择原因")
    with connect() as c:
        task=record(c,"tasks",task_id)
        if task["assignee_id"]!=user["id"] or task["status"]!="in_progress":
            raise PermissionError("只有执行中的负责人可选择候选")
        candidate=c.execute("SELECT * FROM task_candidates WHERE id=? AND task_id=?",(candidate_id,task_id)).fetchone()
        if candidate is None: raise KeyError(candidate_id)
        c.execute("UPDATE task_candidates SET selected=0 WHERE task_id=?",(task_id,))
        c.execute("UPDATE task_candidates SET selected=1,choice_reason=? WHERE id=?",(reason,candidate_id))
        audit(c,user["id"],"candidate.choose",task_id,{"candidate_id":candidate_id})
        return dict(c.execute("SELECT * FROM task_candidates WHERE id=?",(candidate_id,)).fetchone())


def selected_candidate(task_id: str, user: dict) -> dict:
    with connect() as c:
        task=record(c,"tasks",task_id)
        if task["assignee_id"]!=user["id"]: raise PermissionError("只有负责人可提交")
        row=c.execute("SELECT * FROM task_candidates WHERE task_id=? AND selected=1 ORDER BY created_at DESC LIMIT 1",(task_id,)).fetchone()
        if row is None: raise ValueError("请先选择一个候选作品")
        return dict(row)


def create_copilot_message(task_id: str, user: dict, category: str, question: str) -> dict:
    allowed={"task","assets","steps","quality","revision","prompt","next_step","generation","asset_issue","prompt_issue","model_error","clarity","budget","other"}
    question=question.strip()
    if category not in allowed or not question or len(question)>500:
        raise ValueError("问题类型或长度无效")
    with connect() as c:
        task=record(c,"tasks",task_id)
        if not task_visible(c,task,user): raise PermissionError("没有此任务的访问权限")
        if user["role"]!="employee": raise PermissionError("任务助手只供负责人使用")
        pending=c.execute("SELECT COUNT(*) FROM copilot_messages WHERE task_id=? AND user_id=? AND status IN ('queued','dispatching')",(task_id,user["id"])).fetchone()[0]
        if pending: raise ValueError("上一条问题仍在处理")
        mid="cm_"+secrets.token_hex(8)
        c.execute("INSERT INTO copilot_messages(id,task_id,user_id,category,question,status,created_at) VALUES(?,?,?,?,?,?,?)",(mid,task_id,user["id"],category,question,"queued",now()))
        audit(c,user["id"],"copilot.ask",task_id,{"message_id":mid,"category":category})
        return {"id":mid,"status":"queued"}


def resolve_escalation(task_id: str, escalation_id: str, resolution: str, user: dict) -> dict:
    if user["role"] not in {"manager","founder"}:
        raise PermissionError("只有管理者可处理升级事项")
    resolution=resolution.strip()
    if len(resolution)<4 or len(resolution)>1500:
        raise ValueError("请写明处理结论")
    with connect() as c:
        row=c.execute("SELECT * FROM escalations WHERE id=? AND task_id=?",(escalation_id,task_id)).fetchone()
        if row is None:raise KeyError(escalation_id)
        if row["status"]!="open":raise ValueError("该升级事项已经处理")
        c.execute("UPDATE escalations SET status='resolved',resolved_by=?,resolution=?,resolved_at=? WHERE id=?",(user["id"],resolution,now(),escalation_id))
        audit(c,user["id"],"escalation.resolve",task_id,{"escalation_id":escalation_id})
        return {"id":escalation_id,"status":"resolved"}


def employee_outputs(user: dict) -> list[dict]:
    if user["role"]!="employee": raise PermissionError("仅员工可查看个人作品")
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT d.id,d.task_id,d.version,d.asset_id,d.external_url,d.provider_job_id,d.note,d.submitted_at,t.title,t.project_id,t.status FROM deliverables d JOIN tasks t ON t.id=d.task_id WHERE d.submitted_by=? ORDER BY d.submitted_at DESC LIMIT 100",(user["id"],))]


def employee_output_summary(user: dict) -> dict:
    if user["role"]!="employee": raise PermissionError("仅员工可查看个人作品")
    with connect() as c:
        return {
            "submitted_versions":c.execute("SELECT COUNT(*) FROM deliverables WHERE submitted_by=?",(user["id"],)).fetchone()[0],
            "accepted_tasks":c.execute("SELECT COUNT(*) FROM tasks WHERE assignee_id=? AND status='accepted'",(user["id"],)).fetchone()[0],
            "first_pass":c.execute("SELECT COUNT(*) FROM tasks t WHERE t.assignee_id=? AND t.status='accepted' AND t.revision=1 AND NOT EXISTS(SELECT 1 FROM feedback f WHERE f.task_id=t.id)",(user["id"],)).fetchone()[0],
            "revision_tasks":c.execute("SELECT COUNT(DISTINCT task_id) FROM feedback WHERE task_id IN (SELECT id FROM tasks WHERE assignee_id=?)",(user["id"],)).fetchone()[0],
        }


def workspace_summary(user: dict) -> dict:
    with connect() as c:
        if user["role"]=="employee":
            projects=[dict(r) for r in c.execute("SELECT DISTINCT p.id,p.name FROM projects p JOIN tasks t ON t.project_id=p.id WHERE t.assignee_id=? ORDER BY p.name",(user["id"],))]
            return {"projects":projects,"output_summary":employee_output_summary(user)}
        projects=[]
        for p in c.execute("SELECT id,name FROM projects WHERE active=1"):
            counts={r["status"]:r["n"] for r in c.execute("SELECT status,COUNT(*) n FROM tasks WHERE project_id=? GROUP BY status",(p["id"],))}
            projects.append({"id":p["id"],"name":p["name"],"counts":counts,"asset_count":c.execute("SELECT COUNT(*) FROM assets WHERE project_id=?",(p["id"],)).fetchone()[0],"planned_total":None})
        ai=c.execute("SELECT COUNT(*) calls,COALESCE(SUM(CAST(json_extract(usage,'$.prompt_tokens') AS INTEGER)),0) input_tokens,COALESCE(SUM(CAST(json_extract(usage,'$.completion_tokens') AS INTEGER)),0) output_tokens,COUNT(actual_cost) priced_calls,SUM(actual_cost) known_cost FROM costs").fetchone()
        ai_kinds={r["kind"]:r["n"] for r in c.execute("SELECT kind,COUNT(*) n FROM ai_jobs WHERE status='complete' GROUP BY kind")}
        return {"projects":projects,"ai_usage":dict(ai),"ai_kinds":ai_kinds,"needs_review":c.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('technical_checked','ai_prechecked','human_review')").fetchone()[0],"open_escalations":c.execute("SELECT COUNT(*) FROM escalations WHERE status='open'").fetchone()[0],"escalations":[dict(r) for r in c.execute("SELECT e.id,e.task_id,e.reason,e.created_at,t.title FROM escalations e JOIN tasks t ON t.id=e.task_id WHERE e.status='open' ORDER BY e.created_at DESC LIMIT 30")]}


def create_task(spec: dict, actor: str) -> dict:
    pid = spec.get("project_id")
    wid = spec.get("workflow_id")
    title = str(spec.get("title") or "").strip()
    why = str(spec.get("why") or "").strip()
    if pid not in PROJECT_ROOTS or wid not in {"WF-01", "WF-02", "WF-03"} or not title or not why:
        raise ValueError("任务缺少项目、流程、标题或业务目的")
    if len(title) > 180 or len(why) > 1500:
        raise ValueError("任务文本过长")
    budget = float(spec.get("budget_cap", 0))
    if budget < 0 or budget > 100 or budget != budget:
        raise ValueError("预算上限无效")
    inputs = spec.get("input_assets") or []
    if not isinstance(inputs, list) or len(inputs) > 20 or len(set(inputs)) != len(inputs):
        raise ValueError("输入资产无效")
    if wid == "WF-01" and pid != "wuxiang" or wid == "WF-03" and pid != "diaojianghu":
        raise ValueError("流程与项目不匹配")
    tid = "t_" + secrets.token_hex(8)
    with connect() as c:
        for aid in inputs:
            a = record(c, "assets", aid)
            if a["project_id"] != pid or a["status"] != "active":
                raise ValueError("输入资产不属于当前项目或不是有效版本")
        assignee = spec.get("assignee_id")
        if assignee:
            u = record(c, "users", assignee)
            if u["role"] != "employee" or not u["active"]:
                raise ValueError("只能指派有效员工")
        char_lock = spec.get("character_lock")
        motion_lock = spec.get("motion_lock")
        if wid == "WF-01":
            if not isinstance(char_lock, dict) or not isinstance(motion_lock, dict):
                raise ValueError("功法任务必须有角色和动作参考锁定")
            for lock, expected in ((char_lock, {"asset_id", "identity", "version", "forbidden_changes"}), (motion_lock, {"asset_id", "move", "start", "end", "orientation", "key_moments", "version"})):
                if not expected.issubset(lock):
                    raise ValueError("角色或动作参考锁定信息不完整")
                if lock["asset_id"] not in inputs:
                    raise ValueError("锁定资产必须在任务输入中")
            if record(c, "assets", char_lock["asset_id"])["type"] != "character" or record(c, "assets", motion_lock["asset_id"])["type"] != "motion_reference":
                raise ValueError("角色或动作参考类型错误")
        stamp = now()
        c.execute("""INSERT INTO tasks(id,project_id,workflow_id,title,why,assignee_id,priority,due_at,status,context,input_assets,instructions,ai_prepared,deliverable_contract,qc_contract,budget_cap,character_lock,motion_lock,founder_required,created_at,updated_at,created_by)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (tid,pid,wid,title,why,assignee,int(spec.get("priority",2)),spec.get("due_at"),"planned",dumps(spec.get("context") or {}),dumps(inputs),dumps(spec.get("instructions") or []),None,dumps(spec.get("deliverable_contract") or {}),dumps(spec.get("qc_contract") or {}),budget,dumps(char_lock) if char_lock else None,dumps(motion_lock) if motion_lock else None,1 if spec.get("founder_required") else 0,stamp,stamp,actor))
        audit(c,actor,"task.create",tid,{"project_id":pid,"workflow_id":wid})
        return task_public(record(c,"tasks",tid),True)


def update_task_status(c, task_id: str, expected: set[str], new: str, actor: str, reason: str | None = None):
    if new not in STATES:
        raise ValueError("无效任务状态")
    task = record(c, "tasks", task_id)
    if task["status"] not in expected:
        raise ValueError(f"当前状态 {task['status']} 不能转到 {new}")
    c.execute("UPDATE tasks SET status=?,blocked_reason=?,updated_at=? WHERE id=?", (new,reason,now(),task_id))
    audit(c,actor,"task.status",task_id,{"from":task["status"],"to":new,"reason":reason})


def assign(task_id: str, employee_id: str, actor: str):
    with connect() as c:
        task = record(c,"tasks",task_id)
        employee = record(c,"users",employee_id)
        if employee["role"] != "employee" or not employee["active"]:
            raise ValueError("只能指派有效员工")
        if task["status"] != "ready":
            raise ValueError("任务需要先完成 AI 准备")
        c.execute("UPDATE tasks SET assignee_id=? WHERE id=?",(employee_id,task_id))
        update_task_status(c,task_id,{"ready"},"assigned",actor)


def start(task_id: str, user: dict):
    with connect() as c:
        task = record(c,"tasks",task_id)
        if task["assignee_id"] != user["id"]:
            raise PermissionError("只有被指派员工可开始任务")
        update_task_status(c,task_id,{"assigned","revision_required"},"in_progress",user["id"])


def list_today(user: dict):
    with connect() as c:
        if user["role"] == "employee":
            rows = c.execute("SELECT * FROM tasks WHERE assignee_id=? AND status!='accepted'",(user["id"],)).fetchall()
            known={r["id"]:r["status"] for r in c.execute("SELECT id,status FROM tasks")}
            result=[]
            for row in rows:
                item=task_public(dict(row),False)
                dependencies=item["context"].get("dependencies",[]) if isinstance(item["context"],dict) else []
                item["dependency_blocked"]=any(known.get(dep)!="accepted" for dep in dependencies if isinstance(dep,str)) if isinstance(dependencies,list) else False
                result.append(item)
            result.sort(key=lambda t:(0 if t["status"]=="revision_required" else 2 if t["status"]=="blocked" or t["dependency_blocked"] else 1,-t["priority"],t["due_at"] is None,t["due_at"] or ""))
            return result
        else:
            rows = c.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
        return [task_public(dict(r),user["role"]!="employee") for r in rows]


def latest_delivery(c, task_id: str):
    row = c.execute("SELECT * FROM deliverables WHERE task_id=? ORDER BY version DESC LIMIT 1",(task_id,)).fetchone()
    if row is None:
        raise ValueError("尚未提交交付物")
    return dict(row)


def add_delivery(task_id: str,user: dict,asset_id: str | None,external_url: str | None,provider_job_id: str | None,note: str,exception_note: str | None):
    if not any((asset_id,external_url,provider_job_id)):
        raise ValueError("至少提交文件、URL 或平台任务编号")
    if external_url and (not external_url.startswith("https://") or len(external_url)>2000):
        raise ValueError("交付链接必须为 HTTPS")
    if provider_job_id and len(provider_job_id)>200:
        raise ValueError("平台任务编号过长")
    with connect() as c:
        task=record(c,"tasks",task_id)
        if task["assignee_id"]!=user["id"]:
            raise PermissionError("只有被指派员工可提交")
        if task["status"]!="in_progress":
            raise ValueError("任务不在执行中")
        if asset_id and record(c,"assets",asset_id)["project_id"]!=task["project_id"]:
            raise ValueError("交付资产不属于当前项目")
        v=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM deliverables WHERE task_id=?",(task_id,)).fetchone()[0]
        did="d_"+secrets.token_hex(8)
        c.execute("INSERT INTO deliverables(id,task_id,version,asset_id,external_url,provider_job_id,note,exception_note,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(did,task_id,v,asset_id,external_url,provider_job_id,str(note or "")[:3000],str(exception_note or "")[:2000],user["id"],now()))
        c.execute("UPDATE tasks SET revision=?,updated_at=? WHERE id=?",(v,now(),task_id))
        update_task_status(c,task_id,{"in_progress"},"submitted",user["id"])
        audit(c,user["id"],"deliverable.submit",task_id,{"deliverable_id":did,"version":v})
        return dict(record(c,"deliverables",did))


def add_qc(task_id: str,deliverable_id: str,stage: str,result: str,criteria: list,findings: list,reviewer_id: str | None,method: str,coverage: str,evidence_refs: list,actor: str):
    if stage not in {"technical","ai","human","founder"} or result not in {"pass","fail","unverified"}:
        raise ValueError("QC 类型或结果无效")
    with connect() as c:
        task=record(c,"tasks",task_id)
        delivery=record(c,"deliverables",deliverable_id)
        if delivery["task_id"]!=task_id or delivery["version"]!=task["revision"]:
            raise ValueError("只能检查当前提交版本")
        qid="q_"+secrets.token_hex(8)
        c.execute("INSERT INTO qc(id,task_id,deliverable_id,stage,result,criteria,findings,reviewer_id,method,coverage,evidence_refs,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(qid,task_id,deliverable_id,stage,result,dumps(criteria),dumps(findings),reviewer_id,method,coverage,dumps(evidence_refs),now()))
        audit(c,actor,"qc."+stage,task_id,{"qc_id":qid,"result":result,"deliverable_id":deliverable_id})
        return qid


def feedback(task_id: str,delivery_id: str,user: dict,kind: str,problem: str,change_request: str,preserve: str):
    if kind not in FEEDBACK_KINDS or not problem.strip() or not change_request.strip():
        raise ValueError("返修意见须说明问题和修改办法")
    with connect() as c:
        task=record(c,"tasks",task_id)
        delivery=record(c,"deliverables",delivery_id)
        if delivery["task_id"]!=task_id or delivery["version"]!=task["revision"]:
            raise ValueError("返修必须绑定当前版本")
        fid="f_"+secrets.token_hex(8)
        c.execute("INSERT INTO feedback(id,task_id,deliverable_id,version,kind,problem,change_request,preserve,author_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(fid,task_id,delivery_id,delivery["version"],kind,problem[:3000],change_request[:3000],preserve[:3000],user["id"],now()))
        refs=parse(task["feedback_refs"],[])+[fid]
        c.execute("UPDATE tasks SET feedback_refs=? WHERE id=?",(dumps(refs),task_id))
        update_task_status(c,task_id,{"submitted","technical_checked","ai_prechecked","human_review"},"revision_required",user["id"])
        audit(c,user["id"],"feedback.create",task_id,{"feedback_id":fid,"version":delivery["version"]})
        return fid


def candidate_from_feedback(feedback_id: str,user: dict,scope: str,conditions: str,counterexample: str):
    with connect() as c:
        f=c.execute("SELECT * FROM feedback WHERE id=?",(feedback_id,)).fetchone()
        if f is None: raise KeyError(feedback_id)
        cid="cl_"+secrets.token_hex(8)
        c.execute("INSERT INTO candidate_learning(id,feedback_id,task_id,scope,conditions,counterexample,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(cid,feedback_id,f["task_id"],scope,conditions,counterexample,"candidate",user["id"],now()))
        audit(c,user["id"],"learning.candidate",f["task_id"],{"candidate_id":cid})
        return cid


def founder_queue():
    with connect() as c:
        tasks=[task_public(dict(r),True) for r in c.execute("SELECT * FROM tasks WHERE founder_required=1 AND status='human_review' ORDER BY updated_at")]
        projects=[]
        for r in c.execute("SELECT id,name FROM projects WHERE active=1"):
            counts={row[0]:row[1] for row in c.execute("SELECT status,COUNT(*) FROM tasks WHERE project_id=? GROUP BY status",(r["id"],))}
            projects.append({"id":r["id"],"name":r["name"],"counts":counts})
        return {"needs_my_decision":tasks,"project_snapshot":projects}


def manager_overview():
    with connect() as c:
        users=[dict(r) for r in c.execute("SELECT id,username,display_name,role,active FROM users ORDER BY role,display_name")]
        projects=[dict(r) for r in c.execute("SELECT id,name FROM projects WHERE active=1")]
        workflows=[dict(r) for r in c.execute("SELECT id,project_id,name,description FROM workflows WHERE active=1")]
        return {"users":users,"projects":projects,"workflows":workflows,"tasks":list_today({"role":"manager"})}

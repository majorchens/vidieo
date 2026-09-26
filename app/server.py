"""Work OS web application; Company Workflow remains a separate executor."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import re
import secrets
import tempfile
import threading
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import adapters
import asset_center
import ai_studio
import legacy_asset_bridge
import lesson_pipeline
import martial
import martial_assets
import martial_initialization
import martial_multimodal_assets
import martial_product
import store
import workflows

STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 70_000_000
COOKIE_PATH = os.environ.get("YOODUN_BASE_PATH", "/").rstrip("/") + "/"
LOGIN_ATTEMPTS = {}
LOGIN_LOCK = threading.Lock()


class AppError(Exception):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def safe_user(user: dict):
    result={k:user[k] for k in ("id","username","display_name","role")}
    result["martial_specialist"]=martial.specialty(user)
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "YoodunWorkOS/Martial-0.1"

    def log_message(self, fmt, *args):
        return

    def headers_common(self, ctype: str, size: int, code=200, static_cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "private, max-age=86400, immutable" if static_cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://*.aliyuncs.com; media-src 'self' blob: https://*.aliyuncs.com; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

    def send_json(self, value, code=200, cookie=None):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.headers_common("application/json; charset=utf-8",len(body),code)
        if cookie: self.send_header("Set-Cookie",cookie)
        self.end_headers(); self.wfile.write(body)

    def send_file(self,path: Path,ctype: str,static_cache=False):
        body=path.read_bytes()
        self.headers_common(ctype,len(body),static_cache=static_cache); self.end_headers();self.wfile.write(body)

    def send_media_file(self,path: Path,ctype: str):
        """Serve private motion originals with video range requests and bounded memory."""
        size=path.stat().st_size
        if size<=0:raise AppError("视频文件为空",404)
        start,end=0,size-1
        requested=self.headers.get("Range","")
        if requested:
            match=re.fullmatch(r"bytes=(\d+)-(\d*)",requested)
            if not match:raise AppError("不支持此文件范围",416)
            start=int(match.group(1));end=int(match.group(2)) if match.group(2) else size-1
            if start>=size or end<start:raise AppError("文件范围无效",416)
            end=min(end,size-1)
        self.headers_common(ctype,end-start+1,206 if requested else 200)
        self.send_header("Accept-Ranges","bytes")
        if requested:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            remaining=end-start+1
            while remaining:
                chunk=stream.read(min(65536,remaining))
                if not chunk:break
                try:self.wfile.write(chunk)
                except (BrokenPipeError,ConnectionResetError):return
                remaining-=len(chunk)

    def _origin_ok(self):
        host=self.headers.get("Host","")
        if not host or any(x in host for x in ("/","\\","@")):
            return False
        origin=self.headers.get("Origin")
        if origin and origin not in {"http://"+host,"https://"+host}:
            return False
        return True

    def _token(self):
        try:
            cookie=cookies.SimpleCookie();cookie.load(self.headers.get("Cookie",""))
            return cookie["yoodun_session"].value if "yoodun_session" in cookie else ""
        except cookies.CookieError:
            return ""

    def _user(self):
        user=store.session_user(self._token())
        if not user: raise AppError("请先登录",401)
        return user

    def _require_role(self,user,allowed):
        if user["role"] not in allowed: raise AppError("没有此操作权限",403)

    def _check_csrf(self,user):
        if not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),user["csrf"]):
            raise AppError("会话验证失败",403)

    def _connector_auth(self):
        expected=os.environ.get("YOODUN_CONNECTOR_TOKEN","")
        presented=self.headers.get("Authorization","")
        if not expected or not presented.startswith("Bearer ") or not secrets.compare_digest(presented[7:],expected):
            raise AppError("连接器未授权",403)

    def _body(self):
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            raise AppError("需要 JSON 请求",415)
        try: n=int(self.headers.get("Content-Length","0"))
        except ValueError: raise AppError("请求长度无效")
        if n<=0 or n>MAX_BODY: raise AppError("请求大小不合要求",413)
        try: return json.loads(self.rfile.read(n))
        except ValueError: raise AppError("请求不是有效 JSON")

    def _motion_file(self, user: dict, move_id: str):
        """Receive the original video in bounded chunks after auth and preflight."""
        self._check_csrf(user)
        martial.check_motion_upload(user,move_id)
        content_type=self.headers.get("Content-Type","").split(";",1)[0].lower()
        if content_type not in {"video/mp4","video/quicktime"}:
            raise AppError("真人动作需为 MP4/MOV 视频",415)
        try:filename=unquote(self.headers.get("X-File-Name",""),errors="strict")
        except UnicodeDecodeError:raise AppError("视频文件名无效")
        if (not filename or len(filename)>180 or Path(filename).name!=filename or
            "\\" in filename or "\x00" in filename or Path(filename).suffix.lower() not in {".mp4",".mov"}):
            raise AppError("视频文件名无效")
        if (content_type=="video/mp4" and Path(filename).suffix.lower()!=".mp4" or
            content_type=="video/quicktime" and Path(filename).suffix.lower()!=".mov"):
            raise AppError("视频文件类型与文件名不一致",415)
        try:size=int(self.headers.get("Content-Length",""))
        except ValueError:raise AppError("请提供视频文件长度",411)
        if size<=0 or size>store.MOTION_FILE_LIMIT:
            raise AppError("真人动作视频为空或超过 512MB 上限",413)
        claimed_sha=self.headers.get("X-File-SHA256","").lower()
        if claimed_sha and not re.fullmatch(r"[a-f0-9]{64}",claimed_sha):
            raise AppError("视频校验值无效")
        dest_dir=store.production_root()/"wuxiang"/"motion_reference"
        dest_dir.mkdir(parents=True,exist_ok=True)
        staged=None
        try:
            with tempfile.NamedTemporaryFile(mode="wb",prefix=".motion-",suffix=Path(filename).suffix.lower(),
                                             dir=dest_dir,delete=False) as output:
                staged=Path(output.name)
                digest=hashlib.sha256()
                remaining=size
                self.connection.settimeout(120)
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise AppError("视频传输中断，请重新上传")
                    output.write(chunk);digest.update(chunk);remaining-=len(chunk)
                output.flush();os.fsync(output.fileno())
            sha=digest.hexdigest()
            if claimed_sha and claimed_sha!=sha:raise AppError("视频校验失败，请重新上传")
            self.send_json(martial.upload_motion_file(user,move_id,staged,filename,sha),201)
        finally:
            if staged is not None:staged.unlink(missing_ok=True)

    def _candidate_file(self, job_id: str):
        """Receive a connector's already-downloaded MP4 without JSON/base64."""
        self._connector_auth()
        if self.headers.get("Content-Type","").split(";",1)[0].lower()!="video/mp4":
            raise AppError("候选文件需为 MP4 视频",415)
        try:size=int(self.headers.get("Content-Length",""))
        except ValueError:raise AppError("请提供视频文件长度",411)
        if size<=0 or size>store.MOTION_FILE_LIMIT:
            raise AppError("候选视频为空或超过 512MB 上限",413)
        sha=self.headers.get("X-File-SHA256","").lower()
        if not re.fullmatch(r"[a-f0-9]{64}",sha):raise AppError("候选视频校验值无效")
        martial.candidate_upload_state(job_id,sha)
        stage_dir=store.DATA/"connector_staging"/"martial"
        stage_dir.mkdir(parents=True,exist_ok=True)
        staged=None
        try:
            with tempfile.NamedTemporaryFile(mode="wb",prefix=".candidate-",suffix=".mp4",
                                             dir=stage_dir,delete=False) as output:
                staged=Path(output.name)
                digest=hashlib.sha256()
                remaining=size
                self.connection.settimeout(120)
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise AppError("候选视频传输中断，请重试")
                    output.write(chunk);digest.update(chunk);remaining-=len(chunk)
                output.flush();os.fsync(output.fileno())
            if digest.hexdigest()!=sha:raise AppError("候选视频校验失败，请重试")
            self.send_json(martial.stage_candidate_video(job_id,staged,sha),201)
        finally:
            if staged is not None:staged.unlink(missing_ok=True)

    def _reference_clip_file(self, job_id: str, index: int):
        self._connector_auth()
        if self.headers.get("Content-Type","").split(";",1)[0].lower()!="video/mp4":
            raise AppError("参考片段需为 MP4",415)
        try:size=int(self.headers.get("Content-Length",""))
        except ValueError:raise AppError("请提供片段文件长度",411)
        if size<=0 or size>200_000_000:raise AppError("参考片段为空或超过 200MB",413)
        sha=self.headers.get("X-File-SHA256","").lower()
        if not re.fullmatch(r"[a-f0-9]{64}",sha):raise AppError("片段校验值无效")
        stage_dir=store.DATA/"connector_staging"/"martial"/"reference_clips"
        stage_dir.mkdir(parents=True,exist_ok=True)
        staged=None
        try:
            with tempfile.NamedTemporaryFile(mode="wb",prefix=".clip-",suffix=".mp4",dir=stage_dir,delete=False) as output:
                staged=Path(output.name);digest=hashlib.sha256();remaining=size
                self.connection.settimeout(120)
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise AppError("参考片段传输中断",400)
                    output.write(chunk);digest.update(chunk);remaining-=len(chunk)
                output.flush();os.fsync(output.fileno())
            if digest.hexdigest()!=sha:raise AppError("参考片段传输校验失败",400)
            self.send_json(martial.stage_reference_clip(job_id,index,staged,sha),201)
        finally:
            if staged is not None:staged.unlink(missing_ok=True)

    def _ai_video_file(self, artifact_id: str):
        """Receive an AI Studio result as a bounded binary stream."""
        self._connector_auth()
        if self.headers.get("Content-Type","").split(";",1)[0].lower()!="video/mp4":
            raise AppError("AI 视频文件需为 MP4",415)
        try:size=int(self.headers.get("Content-Length",""))
        except ValueError:raise AppError("请提供视频文件长度",411)
        if size<=0 or size>store.MOTION_FILE_LIMIT:
            raise AppError("AI 视频为空或超过 512MB 上限",413)
        sha=self.headers.get("X-File-SHA256","").lower()
        if not re.fullmatch(r"[a-f0-9]{64}",sha):raise AppError("AI 视频校验值无效")
        ai_studio.video_upload_state(artifact_id,sha)
        stage_dir=store.DATA/"connector_staging"/"ai_studio"
        stage_dir.mkdir(parents=True,exist_ok=True)
        staged=None
        try:
            with tempfile.NamedTemporaryFile(mode="wb",prefix=".ai-video-",suffix=".mp4",
                                             dir=stage_dir,delete=False) as output:
                staged=Path(output.name)
                digest=hashlib.sha256()
                remaining=size
                self.connection.settimeout(120)
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise AppError("AI 视频传输中断，请重试")
                    output.write(chunk);digest.update(chunk);remaining-=len(chunk)
                output.flush();os.fsync(output.fileno())
            if digest.hexdigest()!=sha:raise AppError("AI 视频校验失败，请重试")
            self.send_json(ai_studio.stage_video_file(artifact_id,staged,sha),201)
        finally:
            if staged is not None:staged.unlink(missing_ok=True)

    def do_GET(self):
        try:
            if not self._origin_ok(): raise AppError("来源验证失败",403)
            path=urlparse(self.path).path
            if path in {"/","/index.html"}:
                self.send_file(STATIC/"index.html","text/html; charset=utf-8");return
            if path=="/app.js": self.send_file(STATIC/"app.js","application/javascript; charset=utf-8",True);return
            if path=="/martial.js": self.send_file(STATIC/"martial.js","application/javascript; charset=utf-8",True);return
            if path=="/ai_studio.js": self.send_file(STATIC/"ai_studio.js","application/javascript; charset=utf-8",True);return
            if path=="/styles.css": self.send_file(STATIC/"styles.css","text/css; charset=utf-8",True);return
            if path=="/martial.css": self.send_file(STATIC/"martial.css","text/css; charset=utf-8",True);return
            if path=="/ai_studio.css": self.send_file(STATIC/"ai_studio.css","text/css; charset=utf-8",True);return
            if path.startswith("/api/martial/source/"):
                asset_id=path.rsplit("/",1)[-1]
                query=__import__("urllib.parse",fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                source=martial.source_path(asset_id,(query.get("exp") or [""])[0],(query.get("sig") or [""])[0])
                self.send_media_file(source,mimetypes.guess_type(source.name)[0] or "application/octet-stream");return
            clip_match=re.fullmatch(r"/api/martial/clip/(mj_[a-f0-9]{16})/(\d+)",path)
            if clip_match:
                query=__import__("urllib.parse",fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                source=martial.clip_source_path(clip_match.group(1),int(clip_match.group(2)),
                    (query.get("exp") or [""])[0],(query.get("sig") or [""])[0])
                self.send_media_file(source,"video/mp4");return
            if path=="/api/connector/jobs":
                self._connector_auth();self.send_json({"jobs":adapters.connector_jobs()});return
            if path=="/api/martial/connector/packages":
                self._connector_auth();self.send_json({"jobs":martial.package_jobs()});return
            if path=="/api/martial/connector/media":
                self._connector_auth();self.send_json({"jobs":martial.media_jobs()});return
            if path=="/api/ai-studio/connector/jobs":
                self._connector_auth();self.send_json({"jobs":ai_studio.connector_jobs()});return
            user=self._user()
            if path=="/api/ai-studio/overview":
                self.send_json(ai_studio.overview(user));return
            if path=="/api/ai-studio/artifacts":
                query=__import__("urllib.parse",fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                self.send_json(ai_studio.list_artifacts(user,(query.get("capability") or [None])[0],(query.get("project_id") or [None])[0]));return
            if path=="/api/ai-studio/admin":
                self.send_json(ai_studio.admin(user));return
            ai_download=re.fullmatch(r"/api/ai-studio/artifacts/(aia_[a-f0-9]{16})/download",path)
            if ai_download:
                result_path=ai_studio.result_file(user,ai_download.group(1))
                ctype=mimetypes.guess_type(result_path.name)[0] or "application/octet-stream"
                size=result_path.stat().st_size
                start,end=0,size-1
                range_header=self.headers.get("Range","")
                if range_header:
                    match=re.fullmatch(r"bytes=(\d+)-(\d*)",range_header)
                    if not match:raise AppError("不支持此文件范围",416)
                    start=int(match.group(1));end=int(match.group(2)) if match.group(2) else size-1
                    if start>=size or end<start:raise AppError("文件范围无效",416)
                    end=min(end,size-1)
                self.headers_common(ctype,end-start+1,206 if range_header else 200)
                self.send_header("Content-Disposition","inline; filename="+ai_download.group(1)+result_path.suffix)
                self.send_header("Accept-Ranges","bytes")
                if range_header:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                self.end_headers()
                with result_path.open("rb") as stream:
                    stream.seek(start)
                    remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(65536,remaining))
                        if not chunk:break
                        try:self.wfile.write(chunk)
                        except (BrokenPipeError,ConnectionResetError):return
                        remaining-=len(chunk)
                return
            ai_artifact=re.fullmatch(r"/api/ai-studio/artifacts/(aia_[a-f0-9]{16})",path)
            if ai_artifact:
                self.send_json(ai_studio.artifact_detail(user,ai_artifact.group(1)));return
            if path=="/api/asset-center":
                query=__import__("urllib.parse",fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                self.send_json(asset_center.list_assets(user,{key:values[0] for key,values in query.items() if values}));return
            legacy_preview=re.fullmatch(r"/api/asset-center/(uar_[a-f0-9]{16})/legacy-preview",path)
            if legacy_preview:
                signed=legacy_asset_bridge.resolve_signed_access(user,legacy_preview.group(1),self.headers.get("Cookie",""))
                self.send_response(307)
                self.send_header("Location",signed)
                self.send_header("Content-Length","0")
                self.send_header("Cache-Control","no-store")
                self.send_header("Referrer-Policy","no-referrer")
                self.send_header("X-Content-Type-Options","nosniff")
                self.end_headers();return
            asset_media=re.fullmatch(r"/api/asset-center/(uar_[a-f0-9]{16})/media",path)
            if asset_media:
                media=asset_center.media_path(user,asset_media.group(1))
                size=media.stat().st_size
                if size<=0:raise AppError("素材文件为空",404)
                start,end=0,size-1
                range_header=self.headers.get("Range","")
                if range_header:
                    match=re.fullmatch(r"bytes=(\d+)-(\d*)",range_header)
                    if not match:raise AppError("不支持此文件范围",416)
                    start=int(match.group(1));end=int(match.group(2)) if match.group(2) else size-1
                    if start>=size or end<start:raise AppError("文件范围无效",416)
                    end=min(end,size-1)
                self.headers_common(mimetypes.guess_type(media.name)[0] or "application/octet-stream",
                                    end-start+1,206 if range_header else 200)
                self.send_header("Content-Disposition","inline; filename="+asset_media.group(1)+media.suffix)
                self.send_header("Accept-Ranges","bytes")
                if range_header:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                self.end_headers()
                with media.open("rb") as stream:
                    stream.seek(start)
                    remaining=end-start+1
                    while remaining:
                        chunk=stream.read(min(65536,remaining))
                        if not chunk:break
                        try:self.wfile.write(chunk)
                        except (BrokenPipeError,ConnectionResetError):return
                        remaining-=len(chunk)
                return
            asset_match=re.fullmatch(r"/api/asset-center/(uar_[a-f0-9]{16})",path)
            if asset_match:
                self.send_json(asset_center.detail(user,asset_match.group(1)));return
            if path=="/api/martial/assets":
                self.send_json(martial_product.list_assets(user));return
            if path=="/api/martial/overview":
                self.send_json(martial.overview(user));return
            lesson_art=re.fullmatch(r"/api/martial/lesson-dashboard/([a-z0-9_]+)",path)
            if lesson_art:
                self.send_json(lesson_pipeline.art_dashboard(user,lesson_art.group(1)));return
            if path=="/api/martial/lesson-tasks":
                self.send_json({"tasks":lesson_pipeline.task_pool(user)});return
            lesson_detail=re.fullmatch(r"/api/martial/lessons/([a-z0-9_]+)",path)
            if lesson_detail:
                self.send_json(lesson_pipeline.detail(user,lesson_detail.group(1)));return
            if path=="/api/martial/available-assets":
                self.send_json(martial.available_assets(user));return
            if path=="/api/martial/unmapped-assets":
                self.send_json(martial_assets.unmapped(user));return
            multimodal=re.fullmatch(r"/api/martial/(arts|masters|moves)/([a-z0-9_]+)/multimodal",path)
            if multimodal:
                scope,item_id=multimodal.groups()
                result=(martial_multimodal_assets.art_assets(user,item_id) if scope=="arts" else
                        martial_multimodal_assets.master_assets(user,item_id) if scope=="masters" else
                        martial_multimodal_assets.move_assets(user,item_id))
                self.send_json(result);return
            audit_match=re.fullmatch(r"/api/martial/moves/([a-z0-9_]+)/audit-history",path)
            if audit_match:
                self.send_json(martial_product.audit_history(user,audit_match.group(1)));return
            art_match=re.fullmatch(r"/api/martial/arts/([a-z0-9_]+)",path)
            if art_match:
                self.send_json(martial.art_detail(art_match.group(1),user));return
            martial_match=re.fullmatch(r"/api/martial/(moves|masters)/([a-z0-9_]+)",path)
            if martial_match:
                kind,item_id=martial_match.groups()
                self.send_json(martial.move_detail(item_id,user) if kind=="moves" else martial.master_detail(item_id,user));return
            if path=="/api/martial/quote":
                query=__import__("urllib.parse",fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                self.send_json(martial.quote(user,(query.get("move") or [""])[0],(query.get("model") or ["sd2.5"])[0],(query.get("count") or ["1"])[0],(query.get("generation_mode") or ["preview"])[0],(query.get("asset_type") or ["teaching"])[0]));return
            if path=="/api/me":
                self.send_json({"user":safe_user(user),"csrf":user["csrf"]});return
            if path=="/api/today":
                self.send_json({"tasks":store.list_today(user)});return
            if path=="/api/workspace":
                self.send_json(store.workspace_summary(user));return
            if path=="/api/outputs":
                self._require_role(user,{"employee"});self.send_json({"outputs":store.employee_outputs(user),"summary":store.employee_output_summary(user)});return
            if path=="/api/media-tool":
                self.send_json({"url":adapters.MEDIA_STUDIO_URL,"name":"武境 AI 创作中心"});return
            if path=="/api/manager":
                self._require_role(user,{"manager","founder"});self.send_json(store.manager_overview());return
            if path=="/api/founder":
                self._require_role(user,{"founder"});self.send_json(store.founder_queue());return
            if path.startswith("/api/tasks/") and path.count("/")==3:
                task_id=path.split("/")[3];self.send_json(store.detail(task_id,user));return
            if path.startswith("/api/assets/") and path.endswith("/text"):
                asset_id=path.split("/")[3]
                if user["role"]=="employee" and martial_product.asset_is_technical_history(asset_id): raise AppError("此素材仅在技术历史中查看",403)
                with store.connect() as c:asset=store.record(c,"assets",asset_id)
                if not (store.project_allowed(user,asset["project_id"]) or asset["project_id"]=="wuxiang" and martial.specialty(user)):raise AppError("没有资产访问权限",403)
                if asset["storage_ref"].startswith(("https://","studio://")):raise AppError("外部素材没有本地原文")
                asset_path=Path(asset["storage_ref"]).resolve(strict=True)
                upload_root=(store.DATA/"uploads"/asset["project_id"]).resolve()
                if not asset_path.is_relative_to(upload_root):store.valid_asset_path(asset["project_id"],str(asset_path))
                if asset_path.suffix.lower() not in {".md",".txt"} or asset_path.stat().st_size>100_000:raise AppError("此素材不能展开原文")
                content=asset_path.read_text(encoding="utf-8",errors="replace")
                if re.search(r"(?:api[_-]?key|password|token|secret)\s*[:=]\s*\S+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",content,re.I):raise AppError("原文含疑似凭据，不能在线展开",403)
                self.send_json({"name":asset["name"],"text":content});return
            if path.startswith("/api/assets/") and path.endswith("/download"):
                asset_id=path.split("/")[3]
                if user["role"]=="employee" and martial_product.asset_is_technical_history(asset_id): raise AppError("此素材仅在技术历史中查看",403)
                with store.connect() as c: asset=store.record(c,"assets",asset_id)
                if not (store.project_allowed(user,asset["project_id"]) or asset["project_id"]=="wuxiang" and martial.specialty(user)): raise AppError("没有资产访问权限",403)
                if asset["storage_ref"].startswith(("https://","studio://")): raise AppError("远程素材请在原工具打开")
                asset_path=Path(asset["storage_ref"]).resolve(strict=True)
                upload_root=(store.DATA/"uploads"/asset["project_id"]).resolve()
                if not asset_path.is_relative_to(upload_root):
                    store.valid_asset_path(asset["project_id"],str(asset_path))
                download_limit=(store.MOTION_FILE_LIMIT if asset["type"] in {"motion_reference","video"}
                                and asset_path.suffix.lower() in {".mp4",".mov"} else 200_000_000)
                if asset_path.stat().st_size>download_limit: raise AppError("文件过大，请从原工具下载")
                ctype=mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
                size=asset_path.stat().st_size
                inline="inline=1" in urlparse(self.path).query and ctype.startswith(("image/","video/","audio/","application/pdf","text/"))
                start,end=0,size-1
                range_header=self.headers.get("Range","") if inline else ""
                if range_header:
                    match=re.fullmatch(r"bytes=(\d+)-(\d*)",range_header)
                    if not match:raise AppError("不支持此文件范围",416)
                    start=int(match.group(1));end=int(match.group(2)) if match.group(2) else size-1
                    if start>=size or end<start:raise AppError("文件范围无效",416)
                    end=min(end,size-1)
                self.headers_common(ctype,end-start+1,206 if range_header else 200)
                self.send_header("Content-Disposition",("inline" if inline else "attachment")+"; filename="+asset_id+asset_path.suffix)
                self.send_header("Accept-Ranges","bytes")
                if range_header:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
                self.end_headers()
                with asset_path.open("rb") as f:
                    f.seek(start)
                    remaining=end-start+1
                    while remaining:
                        chunk=f.read(min(65536,remaining))
                        if not chunk:break
                        try:self.wfile.write(chunk)
                        except (BrokenPipeError,ConnectionResetError):return
                        remaining-=len(chunk)
                return
            if path.startswith("/api/assets/") and path.count("/")==3:
                asset_id=path.split("/")[3]
                if user["role"]=="employee" and martial_product.asset_is_technical_history(asset_id): raise AppError("此素材仅在技术历史中查看",403)
                with store.connect() as c: asset=store.record(c,"assets",asset_id)
                if not (store.project_allowed(user,asset["project_id"]) or asset["project_id"]=="wuxiang" and martial.specialty(user)): raise AppError("没有资产访问权限",403)
                if user["role"]=="employee":
                    asset={k:asset[k] for k in ("id","project_id","type","name","version","status")}|{"external_url":asset["storage_ref"] if asset["storage_ref"].startswith("https://") else None,"studio":asset["storage_ref"].startswith("studio://")}
                self.send_json(asset);return
            if path.startswith("/api/projects/") and path.endswith("/assets"):
                project_id=path.split("/")[3]
                if not (store.project_allowed(user,project_id) or project_id=="wuxiang" and martial.specialty(user)): raise AppError("没有项目访问权限",403)
                assets=store.list_assets(project_id)
                if user["role"]=="employee" and project_id=="wuxiang":
                    assets=[a for a in assets if not martial_product.asset_is_technical_history(a["id"])]
                if user["role"]=="employee":
                    assets=[{k:a[k] for k in ("id","project_id","type","name","version","status","created_at")}|{"extension":Path(a["storage_ref"]).suffix.lower() if not a["storage_ref"].startswith(("https://","studio://")) else ""} for a in assets]
                self.send_json({"assets":assets});return
            raise AppError("页面不存在",404)
        except (AppError,PermissionError,KeyError,ValueError,FileNotFoundError) as exc:
            self.send_json({"error":str(exc)},getattr(exc,"code",403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,(KeyError,FileNotFoundError)) else 400))
        except Exception:
            self.send_json({"error":"服务暂时不可用"},500)

    def do_POST(self):
        try:
            if not self._origin_ok(): raise AppError("来源验证失败",403)
            path=urlparse(self.path).path
            motion_file=re.fullmatch(r"/api/martial/moves/([a-z0-9_]+)/motion-file",path)
            if motion_file:
                self._motion_file(self._user(),motion_file.group(1));return
            candidate_file=re.fullmatch(r"/api/martial/connector/media/(mj_[a-f0-9]{16})/video-file",path)
            if candidate_file:
                self._candidate_file(candidate_file.group(1));return
            reference_clip=re.fullmatch(r"/api/martial/connector/clips/(mj_[a-f0-9]{16})/(\d+)",path)
            if reference_clip:
                self._reference_clip_file(reference_clip.group(1),int(reference_clip.group(2)));return
            ai_video_file=re.fullmatch(r"/api/ai-studio/connector/jobs/(aia_[a-f0-9]{16})/video-file",path)
            if ai_video_file:
                self._ai_video_file(ai_video_file.group(1));return
            connector_match=re.fullmatch(r"/api/connector/jobs/(aj_[a-f0-9]{16})/(claim|report)",path)
            if connector_match:
                self._connector_auth()
                job_id,action=connector_match.groups()
                data=self._body()
                self.send_json(adapters.connector_claim(job_id) if action=="claim" else adapters.connector_report(job_id,data));return
            if path=="/api/martial/connector/routes/report":
                self._connector_auth();data=self._body()
                self.send_json(martial.route_report(data.get("routes")));return
            martial_connector=re.fullmatch(r"/api/martial/connector/(packages|media)/((?:pkg|mj)_[a-f0-9]{16})/(claim|report)",path)
            if martial_connector:
                self._connector_auth()
                kind,item_id,action=martial_connector.groups()
                data=self._body()
                if kind=="packages":result=martial.package_claim(item_id) if action=="claim" else martial.package_report(item_id,data)
                else:result=martial.media_claim(item_id) if action=="claim" else martial.media_report(item_id,data)
                self.send_json(result);return
            ai_connector=re.fullmatch(r"/api/ai-studio/connector/jobs/(aia_[a-f0-9]{16})/(claim|report)",path)
            if ai_connector:
                self._connector_auth()
                job_id,action=ai_connector.groups()
                data=self._body()
                self.send_json(ai_studio.connector_claim(job_id) if action=="claim" else ai_studio.connector_report(job_id,data));return
            data=self._body()
            if path=="/api/login":
                ip=self.client_address[0]
                with LOGIN_LOCK:
                    count,until=LOGIN_ATTEMPTS.get(ip,(0,0))
                    if until>__import__("time").time():raise AppError("登录尝试过多，请稍后再试",429)
                result=store.authenticate(str(data.get("username") or ""),str(data.get("password") or ""))
                if not result:
                    with LOGIN_LOCK:
                        count+=1;LOGIN_ATTEMPTS[ip]=(count,__import__("time").time()+60 if count>=8 else 0)
                    raise AppError("用户名或密码错误",401)
                with LOGIN_LOCK: LOGIN_ATTEMPTS.pop(ip,None)
                token,csrf,user=result
                self.send_json({"user":safe_user(user),"csrf":csrf},cookie="yoodun_session="+token+"; HttpOnly; SameSite=Strict; Path="+COOKIE_PATH+"; Max-Age=43200"+("; Secure" if COOKIE_PATH!="/" else ""));return
            user=self._user();self._check_csrf(user)
            if path=="/api/ai-studio/run":
                self.send_json(ai_studio.create_run(user,data),201);return
            if path=="/api/ai-studio/admin/budget":
                self.send_json(ai_studio.set_budget(user,data));return
            if path=="/api/ai-studio/admin/route":
                self.send_json(ai_studio.set_route(user,data));return
            if path=="/api/asset-center/admin/legacy-identity":
                self.send_json(legacy_asset_bridge.map_identity(user,
                    str(data.get("legacy_username") or ""),str(data.get("work_user_id") or ""),
                    str(data.get("project_id") or ""),active=data.get("active") is True));return
            ai_action=re.fullmatch(r"/api/ai-studio/artifacts/(aia_[a-f0-9]{16})/(save|task|continue)",path)
            if ai_action:
                artifact_id,action=ai_action.groups()
                result=(ai_studio.save_to_project(user,artifact_id,data) if action=="save" else
                        ai_studio.turn_into_task(user,artifact_id,data) if action=="task" else
                        ai_studio.continue_artifact(user,artifact_id,data))
                self.send_json(result);return
            if path=="/api/asset-center/import-creative-lab":
                self.send_json(asset_center.import_creative_lab(user,data),201);return
            lab_promote=re.fullmatch(r"/api/asset-center/(uar_[a-f0-9]{16})/promote",path)
            if lab_promote:
                self.send_json(asset_center.promote_creative_lab(user,lab_promote.group(1),data));return
            asset_reuse=re.fullmatch(r"/api/asset-center/(uar_[a-f0-9]{16})/reuse",path)
            if asset_reuse:
                self.send_json(asset_center.reuse(user,asset_reuse.group(1),data));return
            if path=="/api/martial/arts":
                self.send_json(martial_product.create_art(user,data),201);return
            if path=="/api/martial/masters":
                self.send_json(martial_product.create_master(user,data),201);return
            art_edit=re.fullmatch(r"/api/martial/arts/([a-z0-9_]+)/edit",path)
            if art_edit:
                self.send_json(martial_product.update_art(user,art_edit.group(1),data));return
            master_edit=re.fullmatch(r"/api/martial/masters/([a-z0-9_]+)/edit",path)
            if master_edit:
                self.send_json(martial_product.update_master(user,master_edit.group(1),data));return
            master_visual=re.fullmatch(r"/api/martial/masters/([a-z0-9_]+)/visual",path)
            if master_visual:
                self.send_json(martial_product.upload_master_visual(user,master_visual.group(1),data),201);return
            if path=="/api/martial/import-asset":
                self.send_json(martial_assets.import_asset(user,data),201);return
            multimodal=re.fullmatch(r"/api/martial/(arts|masters|moves)/([a-z0-9_]+)/(asset|worldview|voice-persona|intro-audio|os-script|tts)",path)
            if multimodal:
                scope,item_id,action=multimodal.groups()
                dispatch={
                    ("arts","asset"):martial_multimodal_assets.set_art_asset,
                    ("arts","worldview"):martial_multimodal_assets.set_art_worldview,
                    ("masters","voice-persona"):martial_multimodal_assets.set_master_voice_persona,
                    ("masters","intro-audio"):martial_multimodal_assets.set_master_intro_audio,
                    ("moves","asset"):martial_multimodal_assets.set_move_asset,
                    ("moves","os-script"):martial_multimodal_assets.save_os_script,
                    ("moves","tts"):martial_multimodal_assets.link_tts_asset,
                }
                handler=dispatch.get((scope,action))
                if handler is None:raise AppError("接口不存在",404)
                self.send_json(handler(user,item_id,data));return
            unmapped_match=re.fullmatch(r"/api/martial/unmapped-assets/(mai_[a-f0-9]{12}_[a-f0-9]{8})/(assign|ignore)",path)
            if unmapped_match:
                item_id,action=unmapped_match.groups()
                self.send_json(martial_assets.assign_unmapped(user,item_id,str(data.get("master_id") or "") if action=="assign" else None,action=="ignore"));return
            art_post=re.fullmatch(r"/api/martial/arts/([a-z0-9_]+)/(draft|approve)",path)
            if art_post:
                art_id,action=art_post.groups()
                self.send_json(martial.save_art(user,art_id,data) if action=="draft" else martial.approve_art(user,art_id));return
            batch_post=re.fullmatch(r"/api/martial/arts/([a-z0-9_]+)/batch-approve",path)
            if batch_post:
                self.send_json(martial.batch_approve_moves(user,batch_post.group(1),data.get("move_ids")));return
            if path=="/api/martial/moves":
                self.send_json(martial_product.create_move(user,data),201);return
            lesson_claim=re.fullmatch(r"/api/martial/lesson-tasks/(t_lesson_[a-z0-9_]+)/claim",path)
            if lesson_claim:
                self.send_json(lesson_pipeline.claim_task(user,lesson_claim.group(1)));return
            lesson_coordination=re.fullmatch(r"/api/martial/lessons/([a-z0-9_]+)/(tasks|work-log)",path)
            if lesson_coordination:
                move_id,action=lesson_coordination.groups()
                self.send_json(lesson_pipeline.ensure_tasks(user,move_id) if action=="tasks" else lesson_pipeline.log_work(user,move_id,data));return
            lesson_action=re.fullmatch(r"/api/martial/lessons/([a-z0-9_]+)/(bind|upload|shot|composition|review|approve|publish)",path)
            if lesson_action:
                move_id,action=lesson_action.groups()
                result=(lesson_pipeline.bind_asset(user,move_id,data) if action=="bind" else
                        lesson_pipeline.upload_asset(user,move_id,data) if action=="upload" else
                        lesson_pipeline.save_shot(user,move_id,data) if action=="shot" else
                        lesson_pipeline.lock_composition(user,move_id,data) if action=="composition" else
                        lesson_pipeline.review(user,move_id,data) if action=="review" else
                        lesson_pipeline.approve(user,move_id) if action=="approve" else
                        lesson_pipeline.publish(user,move_id))
                self.send_json(result);return
            video_plan=re.fullmatch(r"/api/martial/moves/([a-z0-9_]+)/video-plan",path)
            if video_plan:
                self.send_json(martial.save_video_plan(user,video_plan.group(1),data));return
            art_moves=re.fullmatch(r"/api/martial/arts/([a-z0-9_]+)/moves/(batch|import|copy)",path)
            if art_moves:
                art_id,action=art_moves.groups()
                result=(martial_product.create_moves_batch(user,art_id,data) if action=="batch" else
                        martial_product.import_moves(user,art_id,data) if action=="import" else
                        martial_product.copy_moves(user,art_id,data))
                self.send_json(result,201);return
            martial_post=re.fullmatch(r"/api/martial/(moves|masters|motions|media)/([a-z0-9_]+)/(approve|submit|reject|asset|attach|profile|motion|link-motion|range|confirm|cover|standard|package|budget|generate|select|qc|final)",path)
            if martial_post:
                kind,item_id,action=martial_post.groups()
                if kind=="moves" and action=="approve":result=martial.approve_move(user,item_id)
                elif kind=="moves" and action=="submit":result=martial.submit_move(user,item_id,data)
                elif kind=="moves" and action=="reject":result=martial.reject_move(user,item_id,data.get("reason"))
                elif kind=="moves" and action=="standard":result=martial_product.edit_move_standard(user,item_id,data)
                elif kind=="moves" and action=="motion":result=martial.upload_motion(user,item_id,data)
                elif kind=="moves" and action=="link-motion":result=martial.link_motion(user,item_id,data)
                elif kind=="moves" and action=="package":result=martial.request_package(user,item_id,data)
                elif kind=="moves" and action=="budget":result=martial.update_budget(user,item_id,data.get("budget_cap"))
                elif kind=="moves" and action=="generate":result=martial.create_media(user,item_id,data)
                elif kind=="media" and action=="select":result=martial.select_candidate(user,item_id,data.get("reason"))
                elif kind=="masters" and action=="asset":result=martial.upload_master_asset(user,item_id,data)
                elif kind=="masters" and action=="attach":result=martial.attach_master_asset(user,item_id,data)
                elif kind=="masters" and action=="profile":result=martial.save_master_profile(user,item_id,data)
                elif kind=="masters" and action=="approve":result=martial.approve_master(user,item_id)
                elif kind=="motions" and action=="range":result=martial.update_motion_range(user,item_id,data)
                elif kind=="motions" and action=="cover":result=martial.attach_motion_cover(user,item_id,data)
                elif kind=="motions" and action=="confirm":result=martial.confirm_motion(user,item_id)
                elif kind=="media" and action=="qc":result=martial.martial_qc(user,item_id,data)
                elif kind=="media" and action=="final":result=martial.finalize(user,item_id)
                else:raise AppError("接口不存在",404)
                self.send_json(result);return
            if path=="/api/logout":
                store.logout(self._token());self.send_json({"ok":True},cookie="yoodun_session=; HttpOnly; SameSite=Strict; Path="+COOKIE_PATH+"; Max-Age=0"+("; Secure" if COOKIE_PATH!="/" else ""));return
            if path=="/api/users":
                self._require_role(user,{"founder","manager"})
                role=str(data.get("role") or "employee")
                if user["role"]=="manager" and role!="employee":raise AppError("管理者只能新增员工",403)
                uid=store.create_user(str(data.get("username") or ""),str(data.get("display_name") or ""),role,str(data.get("password") or ""),user["id"])
                self.send_json({"id":uid},201);return
            if path=="/api/assets":
                self._require_role(user,{"founder","manager"})
                asset=store.register_asset(str(data.get("project_id") or ""),str(data.get("type") or ""),str(data.get("name") or ""),str(data.get("storage_ref") or ""),user["id"],data.get("source_refs"),source_sha256=data.get("sha256"))
                self.send_json(asset,201);return
            if path=="/api/tasks":
                self._require_role(user,{"founder","manager"});self.send_json(store.create_task(data,user["id"]),201);return
            match=re.fullmatch(r"/api/tasks/([a-z0-9_]+)/([a-z-]+)",path)
            if not match: raise AppError("接口不存在",404)
            task_id,action=match.groups()
            task=store.task_for_user(task_id,user)
            if action=="assign":
                self._require_role(user,{"founder","manager"});store.assign(task_id,str(data.get("employee_id") or ""),user["id"]);self.send_json({"ok":True});return
            if action=="start":
                store.start(task_id,user);self.send_json({"ok":True});return
            if action=="submit":
                self._require_role(user,{"employee"})
                if task["status"]!="in_progress":raise AppError("任务尚未开始")
                asset_id=data.get("asset_id")
                upload=data.get("upload")
                if upload:
                    if asset_id:raise AppError("同一次提交只接受一个文件")
                    try: content=base64.b64decode(upload["base64"],validate=True)
                    except (KeyError,ValueError):raise AppError("上传文件数据无效")
                    asset=store.register_submission_asset(task["project_id"],str(upload.get("name") or ""),content,user["id"])
                    asset_id=asset["id"]
                delivery=store.add_delivery(task_id,user,asset_id,data.get("external_url"),data.get("provider_job_id"),str(data.get("note") or ""),data.get("exception_note"))
                qc=workflows.technical_qc(task_id)
                self.send_json({"deliverable":delivery,"technical_qc":qc},201);return
            if action=="candidate":
                self._require_role(user,{"employee"})
                self.send_json(store.add_candidate(task_id,user,data),201);return
            if action=="upload-candidate":
                self._require_role(user,{"employee"})
                if task["status"]!="in_progress":raise AppError("任务不在执行中")
                upload=data.get("upload") or {}
                try:content=base64.b64decode(upload["base64"],validate=True)
                except (KeyError,ValueError):raise AppError("上传文件数据无效")
                asset=store.register_submission_asset(task["project_id"],str(upload.get("name") or ""),content,user["id"])
                self.send_json({"asset":{"id":asset["id"],"name":asset["name"],"type":asset["type"]}},201);return
            if action=="choose-candidate":
                self._require_role(user,{"employee"})
                self.send_json(store.choose_candidate(task_id,str(data.get("candidate_id") or ""),str(data.get("reason") or ""),user));return
            if action=="submit-selected":
                self._require_role(user,{"employee"})
                candidate=store.selected_candidate(task_id,user)
                delivery=store.add_delivery(task_id,user,candidate["asset_id"],candidate["external_url"],candidate["provider_job_id"],str(data.get("note") or candidate["note"]),str(data.get("exception_note") or ""))
                self.send_json({"deliverable":delivery,"technical_qc":workflows.technical_qc(task_id)},201);return
            if action=="ask":
                self._require_role(user,{"employee"})
                self.send_json(adapters.submit_copilot(task_id,user,str(data.get("category") or "other"),str(data.get("question") or "")),201);return
            if action=="resolve-escalation":
                self._require_role(user,{"manager","founder"})
                self.send_json(store.resolve_escalation(task_id,str(data.get("escalation_id") or ""),str(data.get("resolution") or ""),user));return
            if action=="prepare":
                self._require_role(user,{"founder","manager"});self.send_json(adapters.submit_ai_job(task_id,"prepare",user["id"]),201);return
            if action=="sync-prepare":
                self._require_role(user,{"founder","manager"});self.send_json(adapters.sync_ai_job(task_id,"prepare",user["id"]));return
            if action=="precheck":
                self._require_role(user,{"founder","manager"});self.send_json(adapters.submit_ai_job(task_id,"precheck",user["id"]),201);return
            if action=="sync-precheck":
                self._require_role(user,{"founder","manager"});self.send_json(adapters.sync_ai_job(task_id,"precheck",user["id"]));return
            if action=="skip-precheck":
                self._require_role(user,{"founder","manager"});workflows.ai_skip(task_id,user,str(data.get("reason") or ""));self.send_json({"ok":True});return
            if action=="human-review":
                self._require_role(user,{"founder","manager"});self.send_json(workflows.human_review(task_id,user,str(data.get("verdict") or ""),str(data.get("findings") or ""),str(data.get("change_request") or ""),str(data.get("preserve") or ""),str(data.get("method") or ""),str(data.get("coverage") or ""),data.get("evidence_refs")));return
            if action=="founder-review":
                self._require_role(user,{"founder"});self.send_json(workflows.founder_review(task_id,user,str(data.get("verdict") or ""),str(data.get("findings") or ""),str(data.get("change_request") or ""),str(data.get("preserve") or ""),str(data.get("method") or ""),str(data.get("coverage") or ""),data.get("evidence_refs")));return
            if action=="candidate-learning":
                self._require_role(user,{"founder","manager"});cid=store.candidate_from_feedback(str(data.get("feedback_id") or ""),user,str(data.get("scope") or ""),str(data.get("conditions") or ""),str(data.get("counterexample") or ""));self.send_json({"id":cid},201);return
            if action=="pilot-observation":
                oid=workflows.record_pilot(task_id,user,data);self.send_json({"id":oid},201);return
            raise AppError("接口不存在",404)
        except (AppError,PermissionError,KeyError,ValueError,FileNotFoundError,RuntimeError) as exc:
            self.send_json({"error":str(exc)},getattr(exc,"code",403 if isinstance(exc,PermissionError) else 404 if isinstance(exc,(KeyError,FileNotFoundError)) else 400))
        except Exception:
            self.send_json({"error":"服务暂时不可用"},500)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--host",default="127.0.0.1")
    p.add_argument("--port",type=int,default=18766)
    args=p.parse_args()
    store.initialize()
    martial.initialize()
    martial_initialization.initialize_confirmed_import()
    martial_product.initialize_product_migration()
    martial_multimodal_assets.initialize()
    ai_studio.initialize()
    asset_center.initialize()
    lesson_pipeline.initialize()
    with store.connect() as c:
        liuyun_exists=c.execute("SELECT 1 FROM martial_moves WHERE id='mv_flowing_cloud_01'").fetchone() is not None
    if liuyun_exists:
        lesson_pipeline.ensure_tasks({"id":"u_system","role":"manager"},"mv_flowing_cloud_01")
    legacy_asset_bridge.initialize()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f"Yoodun Work OS http://{args.host}:{args.port}",flush=True)
    server.serve_forever()


if __name__=="__main__":main()

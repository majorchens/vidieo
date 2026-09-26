"""Company-wide AI creation artifacts and remote connector queue.

The ECS stores requests and results. Provider credentials and paid calls stay in
the existing Mac-side Company Workflow process. No model is marked ready merely
because its name appears in a catalogue.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

import store

CAPABILITIES = {"write": "text", "ideas": "concept", "image": "image",
                "video": "video", "analyze": "analysis", "assistant": "text"}
TEXT_CAPABILITIES = {"write", "ideas", "analyze", "assistant"}
VIDEO_PRICES = {"sd2.0": ("doubao-seedance-2.0", 2.5),
                "sd2.5": ("doubao-seedance-2-5", 3.6)}
PRICE_SOURCE = "https://runy.yitd.cn/app/workbench/billing（2026-09-21 实测及保守预留）"
IMAGE_MODEL = "jimeng_t2i_v40"
IMAGE_PRICE = 0.172
IMAGE_PRICE_SOURCE = "https://www.wjark.com/center/model（2026-09-18 单张估价；实际以万界账单为准）"
IMAGE_WIDTH, IMAGE_HEIGHT = 2560, 1440
DEFAULT_PROJECT_CAP = 20.0
DEFAULT_USER_CAP = 10.0
MAX_TEXT = 18_000
MAX_FILE = 5_000_000
MAX_VIDEO = 40_000_000
MAX_IMAGE = 12_000_000
VIDEO_FILE_LIMIT = store.MOTION_FILE_LIMIT
SENSITIVE = re.compile(
    r"(?:api[_-]?key|password|passwd|secret|access[_-]?token|authorization)\s*[:=]\s*['\"]?[^\s,'\"]{6,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I)
BAD_NAME = re.compile(r"(?:^|[._-])(auth|token|secret|credential|password|api[-_]?key|env)(?:[._-]|$)", re.I)
XML_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
          "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SAVE_LOCK = threading.Lock()
VIDEO_UPLOAD_LOCK = threading.Lock()


def initialize() -> None:
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS ai_studio_artifacts(
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            project_id TEXT REFERENCES projects(id),
            task_id TEXT REFERENCES tasks(id),
            parent_id TEXT REFERENCES ai_studio_artifacts(id),
            capability TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            inputs TEXT NOT NULL,
            output TEXT,
            provider TEXT,
            model TEXT,
            model_alias TEXT,
            request_key TEXT NOT NULL UNIQUE,
            input_hash TEXT NOT NULL,
            reserved_cost REAL,
            actual_cost REAL,
            usage TEXT,
            provider_job_id TEXT,
            local_job_id TEXT,
            local_media_id TEXT,
            file_ref TEXT,
            asset_id TEXT REFERENCES assets(id),
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_ai_studio_user ON ai_studio_artifacts(user_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_ai_studio_project ON ai_studio_artifacts(project_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_ai_studio_status ON ai_studio_artifacts(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_ai_studio_hash ON ai_studio_artifacts(user_id,input_hash,status);
        CREATE TABLE IF NOT EXISTS ai_studio_budgets(
            scope TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            cap_cny REAL NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL REFERENCES users(id),
            PRIMARY KEY(scope,scope_id)
        );
        CREATE TABLE IF NOT EXISTS ai_studio_routes(
            business_model TEXT PRIMARY KEY,
            primary_provider TEXT,
            enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_studio_task_links(
            artifact_id TEXT PRIMARY KEY REFERENCES ai_studio_artifacts(id),
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id)
        );
        """)
        c.execute("INSERT OR IGNORE INTO workflows(id,project_id,name,description,version,input_contract,qc_contract) VALUES(?,?,?,?,?,?,?)",
                  ("WF-AI", None, "AI 创作后续任务", "AI 创作成果转为项目任务", "0.1", "{}", "{}"))
        for business_model,provider,enabled in (("text","wanjie",1),("sd2.0","runy",1),
                                                ("sd2.5","runy",1),("image",None,0)):
            c.execute("INSERT OR IGNORE INTO ai_studio_routes(business_model,primary_provider,enabled,updated_at,updated_by) VALUES(?,?,?,?,?)",
                      (business_model,provider,enabled,store.now(),"system"))


def _user_projects(c, user: dict) -> list[dict]:
    if user["role"] in {"founder", "manager"}:
        rows = c.execute("SELECT id,name FROM projects WHERE active=1 ORDER BY name")
    else:
        rows = c.execute("""SELECT DISTINCT p.id,p.name FROM projects p JOIN tasks t ON t.project_id=p.id
                            WHERE p.active=1 AND t.assignee_id=? ORDER BY p.name""", (user["id"],))
    result = [dict(row) for row in rows]
    if user["role"] == "employee" and c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_specialists'").fetchone():
        if c.execute("SELECT 1 FROM martial_specialists WHERE user_id=? AND project_id='wuxiang' AND active=1", (user["id"],)).fetchone():
            row = c.execute("SELECT id,name FROM projects WHERE id='wuxiang' AND active=1").fetchone()
            if row and all(p["id"] != "wuxiang" for p in result): result.append(dict(row))
    return result


def _project(c, user: dict, project_id: str | None) -> dict | None:
    if not project_id: return None
    row = c.execute("SELECT id,name,workflow_project FROM projects WHERE id=? AND active=1", (project_id,)).fetchone()
    if row is None: raise ValueError("项目不存在或已停用")
    if user["role"] == "employee" and project_id not in {p["id"] for p in _user_projects(c,user)}:
        raise PermissionError("没有此项目的访问权限")
    return dict(row)


def _task(c, user: dict, task_id: str | None, project_id: str | None) -> dict | None:
    if not task_id: return None
    row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None: raise ValueError("任务不存在")
    task = dict(row)
    if not project_id or task["project_id"] != project_id: raise ValueError("任务与项目不一致")
    if not store.task_visible(c,task,user): raise PermissionError("没有此任务的访问权限")
    return task


def _route(c, alias: str) -> dict | None:
    if alias not in VIDEO_PRICES: return None
    policy=c.execute("SELECT primary_provider,enabled FROM ai_studio_routes WHERE business_model=?",(alias,)).fetchone()
    if policy is None or not policy["enabled"] or policy["primary_provider"]!="runy":return None
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_routes'").fetchone(): return None
    row = c.execute("SELECT * FROM martial_routes WHERE model_alias=?", (alias,)).fetchone()
    if not row or row["provider"] != "runy" or row["model"] != VIDEO_PRICES[alias][0]: return None
    try: age = time.time() - datetime.fromisoformat(row["checked_at"]).timestamp()
    except (ValueError, TypeError): return None
    return dict(row) if age >= 0 and age <= 90 else None


def _has_recent_text_success(c) -> bool:
    return bool(c.execute("SELECT 1 FROM costs WHERE provider='万界' AND model='deepseek-v4.1-flash' LIMIT 1").fetchone() or
                c.execute("SELECT 1 FROM ai_studio_artifacts WHERE capability IN ('write','ideas','analyze','assistant') AND status='completed' LIMIT 1").fetchone() or
                (c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_packages'").fetchone() and
                 c.execute("SELECT 1 FROM martial_packages WHERE status='complete' LIMIT 1").fetchone()))


def _text_enabled(c) -> bool:
    row=c.execute("SELECT primary_provider,enabled FROM ai_studio_routes WHERE business_model='text'").fetchone()
    return bool(row and row["enabled"] and row["primary_provider"]=="wanjie")


def _image_enabled(c) -> bool:
    row=c.execute("SELECT primary_provider,enabled FROM ai_studio_routes WHERE business_model='image'").fetchone()
    return bool(row and row["enabled"] and row["primary_provider"]=="wanjie")


def _has_image_success(c) -> bool:
    return bool(c.execute("SELECT 1 FROM ai_studio_artifacts WHERE capability='image' AND provider='wanjie' AND model=? AND status='completed' AND file_ref IS NOT NULL AND actual_cost IS NOT NULL LIMIT 1",(IMAGE_MODEL,)).fetchone())


def overview(user: dict) -> dict:
    with store.connect() as c:
        route25 = _route(c,"sd2.5")
        route20 = _route(c,"sd2.0")
        text_status = "DISABLED" if not _text_enabled(c) else "PRODUCTION_READY" if _has_recent_text_success(c) else "TESTED"
        video_status = "PRODUCTION_READY" if route25 or route20 else "TESTED"
        image_enabled = _image_enabled(c)
        image_status = "PRODUCTION_READY" if image_enabled and _has_image_success(c) else "CONFIGURED" if image_enabled else "DISABLED"
        text = {"status": text_status, "display_status": "可用" if text_status == "PRODUCTION_READY" else "等待本机连接器"}
        formats=["TXT","MD","CSV","DOCX","XLSX"]
        if _pdf_available(): formats.append("PDF")
        return {"projects": _user_projects(c,user), "capabilities": {
            "write": dict(text), "ideas": dict(text),
            "analyze": {**text,"file_formats":formats,"image_analysis":False},
            "assistant": dict(text),
            "image": {"status": image_status, "enabled": image_enabled,
                      "display_status": "可用：即梦 4.0" if image_status == "PRODUCTION_READY" else
                                        "图片路由已配置，等待真实生成验收" if image_enabled else "暂未接通",
                      "input_modes": ["文生图"], "reference_image": False,
                      "size": f"{IMAGE_WIDTH}x{IMAGE_HEIGHT}", "count": 1},
            "video": {"status": video_status,
                      "display_status": "可用：Seedance 2.5" if route25 else "可用：Seedance 2.0" if route20 else "等待本机视频路由",
                      "models": {"sd2.0": "PRODUCTION_READY" if route20 else "TESTED",
                                 "sd2.5": "PRODUCTION_READY" if route25 else "TESTED"}},
            "speech": {"status":"PARTIAL","display_status":"语音服务待恢复验证"},
            "audio": {"status":"DISABLED","display_status":"暂未接通"},
            "music": {"status":"DISABLED","display_status":"暂未接通"},
        }}


def _public(row: dict, user: dict, detail=False) -> dict:
    keys = ("id","user_id","project_id","task_id","parent_id","capability","type","status","output",
            "asset_id","error","created_at","updated_at","completed_at","reserved_cost","actual_cost")
    result = {key: row.get(key) for key in keys}
    result["output"] = store.parse(result["output"],None)
    if detail:
        raw = store.parse(row.get("inputs"),{})
        result["inputs"] = {key:value for key,value in raw.items() if key not in {"file_text","asset_texts","project_context"}}
        result["usage"] = store.parse(row.get("usage"),None)
    if user["role"] in {"founder","manager"}:
        result["provider"] = row.get("provider")
        result["model"] = row.get("model")
        result["provider_job_id"] = row.get("provider_job_id")
    return result


def list_artifacts(user: dict, capability: str | None = None, project_id: str | None = None) -> dict:
    if capability and capability not in CAPABILITIES: raise ValueError("成果类型无效")
    with store.connect() as c:
        if project_id: _project(c,user,project_id)
        where = ["1=1"]
        params: list = []
        if user["role"] == "employee": where.append("user_id=?"); params.append(user["id"])
        if capability: where.append("capability=?"); params.append(capability)
        if project_id: where.append("project_id=?"); params.append(project_id)
        rows = c.execute("SELECT * FROM ai_studio_artifacts WHERE " + " AND ".join(where) +
                         " ORDER BY created_at DESC LIMIT 100", params).fetchall()
        return {"artifacts": [_public(dict(row),user) for row in rows]}


def _artifact(c, user: dict, artifact_id: str) -> dict:
    row = c.execute("SELECT * FROM ai_studio_artifacts WHERE id=?", (artifact_id,)).fetchone()
    if row is None: raise KeyError(artifact_id)
    item = dict(row)
    if user["role"] == "employee" and item["user_id"] != user["id"]:
        raise PermissionError("不能访问其他人的 AI 成果")
    return item


def artifact_detail(user: dict, artifact_id: str) -> dict:
    with store.connect() as c: return {"artifact": _public(_artifact(c,user,artifact_id),user,True)}


def result_file(user: dict, artifact_id: str) -> Path:
    with store.connect() as c:
        item = _artifact(c,user,artifact_id)
        if item["status"] != "completed" or item["type"] not in {"video","image"} or not item["file_ref"]:
            raise ValueError("此成果没有可下载媒体")
        path = Path(item["file_ref"]).resolve(strict=True)
        if not path.is_relative_to((store.DATA/"ai_studio"/"results").resolve()):
            raise PermissionError("结果路径无效")
        return path


def _text_field(value, name: str, limit: int) -> str:
    if value is None: return ""
    if not isinstance(value,str): raise ValueError(name+"格式无效")
    text = value.strip()
    if len(text) > limit: raise ValueError(name+"太长")
    if SENSITIVE.search(text): raise ValueError(name+"含疑似凭据，未提交模型")
    return text


def _safe_error(value) -> str | None:
    if value is None:return None
    text=str(value)[:800]
    if SENSITIVE.search(text) or re.search(r"\bsk-[A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.",text):
        return "执行错误（详细信息含疑似凭据，已隐藏）"
    return text[:400] or None


def _xlsx_text(contents: bytes) -> str:
    with _checked_zip(contents) as z:
        names=z.namelist()
        shared=[]
        if "xl/sharedStrings.xml" in names:
            root=ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared=["".join(t.text or "" for t in si.findall(".//s:t",XML_NS)) for si in root.findall("s:si",XML_NS)]
        sheets=[n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml",n)]
        out=[]
        for name in sheets[:8]:
            root=ET.fromstring(z.read(name))
            rows=[]
            for row in root.findall(".//s:sheetData/s:row",XML_NS)[:300]:
                values=[]
                for cell in row.findall("s:c",XML_NS)[:40]:
                    value=cell.find("s:v",XML_NS)
                    inline=cell.find("s:is",XML_NS)
                    text=(value.text or "") if value is not None else "".join(t.text or "" for t in inline.findall(".//s:t",XML_NS)) if inline is not None else ""
                    if cell.attrib.get("t")=="s" and text.isdigit() and int(text)<len(shared): text=shared[int(text)]
                    values.append(text[:500])
                rows.append("\t".join(values))
            out.append(name+"\n"+"\n".join(rows))
        return "\n\n".join(out)


def _checked_zip(contents: bytes) -> zipfile.ZipFile:
    try: archive=zipfile.ZipFile(io.BytesIO(contents))
    except zipfile.BadZipFile as exc:raise ValueError("Office 文件结构无效") from exc
    infos=archive.infolist()
    if len(infos)>500 or any(i.file_size>8_000_000 for i in infos) or sum(i.file_size for i in infos)>20_000_000:
        archive.close()
        raise ValueError("Office 文件解压体积超限")
    return archive


def _pdf_available() -> bool:
    return bool(shutil.which("pdftotext") or importlib.util.find_spec("pypdf") or
                importlib.util.find_spec("pdfplumber"))


def _pdf_text(contents: bytes) -> str:
    extractor=shutil.which("pdftotext")
    if extractor:
        import tempfile
        with tempfile.TemporaryDirectory(prefix="ai-studio-pdf-") as tmp:
            path=Path(tmp)/"source.pdf";path.write_bytes(contents)
            result=subprocess.run([extractor,"-layout",str(path),"-"],capture_output=True,timeout=20,check=False)
            if result.returncode:raise ValueError("PDF 文本提取失败或文档为扫描件")
            return result.stdout.decode("utf-8",errors="replace")
    if importlib.util.find_spec("pypdf"):
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(contents),strict=False)
        if len(reader.pages)>100:raise ValueError("PDF 超过 100 页，请缩小范围")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if importlib.util.find_spec("pdfplumber"):
        import pdfplumber
        with pdfplumber.open(io.BytesIO(contents)) as pdf:
            if len(pdf.pages)>100:raise ValueError("PDF 超过 100 页，请缩小范围")
            return "\n\n".join((page.extract_text() or "") for page in pdf.pages)
    raise ValueError("服务器尚无 PDF 文本提取器；请先转为 TXT、DOCX 或 Markdown")


def _extract_file(upload: dict) -> tuple[str,str,str]:
    if not isinstance(upload,dict): raise ValueError("上传文件格式无效")
    name=Path(str(upload.get("name") or "")).name[:160]
    if not name or BAD_NAME.search(name) or name.startswith("."): raise ValueError("疑似凭据文件不能上传")
    raw=upload.get("base64")
    if not isinstance(raw,str) or len(raw)>MAX_FILE*4//3+8: raise ValueError("文件为空或超过 5MB")
    try: contents=base64.b64decode(raw,validate=True)
    except ValueError as exc: raise ValueError("文件编码无效") from exc
    if not contents or len(contents)>MAX_FILE: raise ValueError("文件为空或超过 5MB")
    suffix=Path(name).suffix.lower()
    if suffix in {".txt",".md",".csv"}:
        text=contents.decode("utf-8-sig",errors="strict")
    elif suffix==".docx":
        with _checked_zip(contents) as z:
            root=ET.fromstring(z.read("word/document.xml"))
            text="\n".join("".join(n.text or "" for n in p.findall(".//w:t",XML_NS))
                           for p in root.findall(".//w:p",XML_NS))
    elif suffix==".xlsx": text=_xlsx_text(contents)
    elif suffix==".pdf":text=_pdf_text(contents)
    elif suffix in {".png",".jpg",".jpeg",".webp"}:
        raise ValueError("当前文本模型没有已验证的图片理解能力，不能分析图片")
    else: raise ValueError("文件类型尚不支持分析")
    text=text.strip()
    if not text: raise ValueError("文件没有可提取的文本")
    if len(text)>MAX_TEXT: text=text[:MAX_TEXT]+"\n[文件内容已截断]"
    if SENSITIVE.search(text): raise ValueError("文件含疑似凭据，未提交模型")
    return name,text,hashlib.sha256(contents).hexdigest()


def _selected_assets(c, user: dict, project_id: str | None, ids) -> list[dict]:
    if ids in (None,[]): return []
    if not project_id or not isinstance(ids,list) or len(ids)>5 or len(set(ids))!=len(ids):
        raise ValueError("参考素材必须属于所选项目，且最多 5 件")
    result=[]
    for aid in ids:
        row=c.execute("SELECT * FROM assets WHERE id=? AND project_id=? AND status='active'",(aid,project_id)).fetchone()
        if not row: raise PermissionError("参考素材不属于当前项目")
        asset=dict(row); ref=asset["storage_ref"]
        if ref.startswith(("https://","studio://")): raise ValueError("远程素材当前不能直接供文本模型读取")
        path=Path(ref).resolve(strict=True)
        upload_root=(store.DATA/"uploads"/project_id).resolve()
        if not path.is_relative_to(upload_root): store.valid_asset_path(project_id,str(path))
        if path.suffix.lower() not in {".md",".txt",".csv",".docx",".xlsx",".pdf"} or path.stat().st_size>MAX_FILE:
            raise ValueError("选中的素材当前无法安全提取文本")
        name,text,sha=_extract_file({"name":path.name,"base64":base64.b64encode(path.read_bytes()).decode()})
        result.append({"asset_id":aid,"name":name,"sha256":sha,"text":text[:5000]})
    return result


def _active_context(c, project_id: str | None, task: dict | None, user: dict) -> dict:
    context={"user_role":user["role"]}
    if project_id:
        p=c.execute("SELECT id,name FROM projects WHERE id=?",(project_id,)).fetchone()
        context["project"]={"id":p["id"],"name":p["name"]}
        # Metadata only: arbitrary knowledge content_ref paths are not trusted inputs.
        context["active_knowledge"]=[dict(r) for r in c.execute(
            "SELECT source,scope,version FROM knowledge WHERE status='active' AND (scope=? OR scope='cross_project') LIMIT 12",(project_id,))]
    if task:
        context["task"]={"id":task["id"],"title":task["title"],"status":task["status"],
                         "context":store.parse(task["context"],{})}
    return context


def _budget_cap(c, scope: str, scope_id: str, default: float) -> float:
    row=c.execute("SELECT cap_cny FROM ai_studio_budgets WHERE scope=? AND scope_id=?",(scope,scope_id)).fetchone()
    return float(row[0]) if row else default


def _spent(c, scope: str, scope_id: str) -> float:
    where={"project":"project_id=?","user":"user_id=?","task":"task_id=?"}[scope]
    amount=c.execute("SELECT COALESCE(SUM(COALESCE(actual_cost,reserved_cost,0)),0) FROM ai_studio_artifacts WHERE "+where,(scope_id,)).fetchone()[0]
    # Include existing martial media commitments in the same project/user/task caps.
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_media_jobs'").fetchone():
        mwhere={"project":"t.project_id=?","user":"t.assignee_id=?","task":"m.task_id=?"}[scope]
        amount += c.execute("""SELECT COALESCE(SUM(COALESCE(m.actual_cost,m.reserved_cost,0)),0)
                                FROM martial_media_jobs m JOIN tasks t ON t.id=m.task_id WHERE """+mwhere,(scope_id,)).fetchone()[0]
    return float(amount)


def _check_budget(c, user: dict, project_id: str | None, task: dict | None, amount: float) -> None:
    if not project_id: raise ValueError("媒体生成须先选择项目")
    project_cap=_budget_cap(c,"project",project_id,DEFAULT_PROJECT_CAP)
    user_cap=_budget_cap(c,"user",user["id"],DEFAULT_USER_CAP)
    if _spent(c,"project",project_id)+amount>project_cap+1e-8: raise ValueError("项目 AI 预算不足，需要负责人调整额度")
    if _spent(c,"user",user["id"])+amount>user_cap+1e-8: raise ValueError("个人 AI 预算不足，需要负责人调整额度")
    if task and _spent(c,"task",task["id"])+amount>float(task["budget_cap"])+1e-8:
        raise ValueError("任务预算不足，需要负责人调整额度")


def create_run(user: dict, data: dict) -> dict:
    if not isinstance(data,dict): raise ValueError("请求格式无效")
    capability=str(data.get("capability") or "")
    if capability not in CAPABILITIES: raise ValueError("未知 AI 能力")
    project_id=str(data.get("project_id") or "") or None
    task_id=str(data.get("task_id") or "") or None
    prompt=_text_field(data.get("prompt"),"工作要求",12000)
    requirements=_text_field(data.get("requirements"),"补充要求",5000)
    scenario=_text_field(data.get("scenario"),"场景",100)
    language=str(data.get("language") or "zh")
    if language not in {"zh","en","both"}: raise ValueError("输出语言无效")
    if not prompt: raise ValueError("请写明工作目标")
    if data.get("image_file") or data.get("video_file"):
        raise ValueError("当前 AI 创作视频仅验证文生视频；图片／视频参考请使用武学工作台已验证流程")
    if capability=="video" and data.get("file"): raise ValueError("当前视频路由不支持文件参考")
    if capability=="image":
        if data.get("file") or data.get("asset_ids"):
            raise ValueError("当前图片路由仅验证文生图，参考图和项目素材尚未接通")
        if data.get("count",1)!=1 or data.get("width",IMAGE_WIDTH)!=IMAGE_WIDTH or data.get("height",IMAGE_HEIGHT)!=IMAGE_HEIGHT:
            raise ValueError("当前图片路由仅支持已报价的单张 2560×1440")
    name=file_text=file_sha=None
    if data.get("file"):
        name,file_text,file_sha=_extract_file(data["file"])
    if capability=="analyze" and not file_text and not data.get("asset_ids"):
        raise ValueError("分析文件需要上传文本文件或选择项目素材")
    if capability!="video" and len(prompt)+len(requirements)+(len(file_text) if file_text else 0)>MAX_TEXT+12000:
        raise ValueError("本次文本输入太长，请缩小文件范围")
    alias=str(data.get("model_alias") or "sd2.5")
    parent_id=str(data.get("parent_id") or "") or None
    with store.connect() as c:
        project=_project(c,user,project_id)
        task=_task(c,user,task_id,project_id)
        if parent_id:_artifact(c,user,parent_id)
        assets=_selected_assets(c,user,project_id,data.get("asset_ids"))
        context=_active_context(c,project_id,task,user)
        provider=model=reserved=None
        if capability=="image":
            if not project or not project.get("workflow_project"):
                raise ValueError("图片生成须先选择已登记的项目")
            if not _image_enabled(c):
                raise ValueError("管理员尚未启用万界图片路由")
            provider="wanjie";model=IMAGE_MODEL;reserved=IMAGE_PRICE
            cap=data.get("budget_cap")
            if cap is not None and (isinstance(cap,bool) or not isinstance(cap,(int,float)) or
                                    not math.isfinite(cap) or cap<reserved):
                raise ValueError("本次预算上限低于图片生成费用预留")
            _check_budget(c,user,project_id,task,reserved)
        elif capability=="video":
            if alias not in VIDEO_PRICES: raise ValueError("视频模型尚无已验证报价")
            if not project or project_id not in {"wuxiang","diaojianghu"}:
                raise ValueError("此项目的视频执行账本尚未接通")
            duration=data.get("duration",5)
            count=data.get("count",1)
            ratio=str(data.get("ratio") or "16:9")
            if duration!=5 or count!=1 or ratio!="16:9":
                raise ValueError("当前只有 5 秒、480p、16:9、1 条候选有已验证报价")
            route=_route(c,alias)
            if not route: raise ValueError("本机润元视频路由尚未在线，不能提交付费请求")
            provider="runy";model=route["model"];reserved=VIDEO_PRICES[alias][1]
            cap=data.get("budget_cap")
            if cap is not None and (isinstance(cap,bool) or not isinstance(cap,(int,float)) or
                                    not math.isfinite(cap) or cap<reserved):
                raise ValueError("本次预算上限低于已验证报价")
            _check_budget(c,user,project_id,task,reserved)
        elif not _text_enabled(c):
            raise ValueError("管理员已停用文本模型路由")
        elif not _has_recent_text_success(c):
            raise ValueError("万界 DeepSeek 尚无本系统成功调用证据，暂不可提交")
        else:
            provider="wanjie";model="deepseek-v4.1-flash"
        inputs={"prompt":prompt,"requirements":requirements,"scenario":scenario,"language":language,
                "file_name":name,"file_sha256":file_sha,"file_text":file_text,
                "asset_texts":assets,"project_context":context}
        if capability=="video":
            inputs.update({"model_alias":alias,"duration":5,"resolution":"480p","ratio":"16:9", "count":1})
        elif capability=="image":
            inputs.update({"width":IMAGE_WIDTH,"height":IMAGE_HEIGHT,"count":1})
        fingerprint=hashlib.sha256(store.dumps({"user":user["id"],"project":project_id,"task":task_id,
                "capability":capability,"inputs":inputs}).encode()).hexdigest()
        if capability in {"video","image"}:
            existing=c.execute("SELECT * FROM ai_studio_artifacts WHERE user_id=? AND input_hash=? AND status='unknown_submission' ORDER BY created_at DESC LIMIT 1",
                               (user["id"],fingerprint)).fetchone()
            if existing: raise ValueError("相同媒体请求尚处于提交待核实状态，不能再次付费提交")
            existing=c.execute("SELECT * FROM ai_studio_artifacts WHERE user_id=? AND input_hash=? AND created_at>=datetime('now','-2 minutes') ORDER BY created_at DESC LIMIT 1",
                               (user["id"],fingerprint)).fetchone()
            # Timestamps contain timezone offsets; compare in Python for portability.
            if existing:
                try: recent=time.time()-datetime.fromisoformat(existing["created_at"]).timestamp()<120
                except (ValueError,TypeError): recent=False
                if recent:return {"artifact":_public(dict(existing),user,True)}
        aid="aia_"+secrets.token_hex(8)
        request_key="workos:ai-studio:"+aid
        stamp=store.now()
        c.execute("""INSERT INTO ai_studio_artifacts(id,user_id,project_id,task_id,parent_id,capability,type,status,
            inputs,provider,model,model_alias,request_key,input_hash,reserved_cost,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid,user["id"],project_id,task_id,parent_id,capability,CAPABILITIES[capability],"queued",
             store.dumps(inputs),provider,model,alias if capability=="video" else None,request_key,fingerprint,reserved,stamp,stamp))
        store.audit(c,user["id"],"ai_studio.run",task_id,{"artifact_id":aid,"capability":capability,"project_id":project_id})
        return {"artifact":_public(dict(c.execute("SELECT * FROM ai_studio_artifacts WHERE id=?",(aid,)).fetchone()),user,True)}


def connector_jobs() -> list[dict]:
    with store.connect() as c:
        rows=c.execute("""SELECT id,capability,status,request_key,local_media_id,provider_job_id FROM ai_studio_artifacts
                          WHERE status IN ('queued','dispatching','submitted','running','download_pending')
                             OR (status='unknown_submission' AND local_media_id IS NOT NULL)
                          ORDER BY created_at LIMIT 12""").fetchall()
        return [dict(row) for row in rows]


def connector_claim(artifact_id: str) -> dict:
    with store.connect() as c:
        row=c.execute("SELECT * FROM ai_studio_artifacts WHERE id=?",(artifact_id,)).fetchone()
        if row is None: raise KeyError(artifact_id)
        job=dict(row)
        if job["status"] not in {"queued","dispatching","submitted","running","download_pending","unknown_submission"}:
            raise ValueError("AI 任务不可认领")
        if job["status"]=="unknown_submission" and not job["local_media_id"]:
            raise ValueError("未知提交没有可查询的原任务")
        if job["status"]=="queued":
            c.execute("UPDATE ai_studio_artifacts SET status='dispatching',updated_at=? WHERE id=?",(store.now(),artifact_id))
        inputs=store.parse(job["inputs"],{})
        project=c.execute("SELECT workflow_project FROM projects WHERE id=?",(job["project_id"],)).fetchone() if job["project_id"] else None
        spec={"id":artifact_id,"capability":job["capability"],"status":job["status"],
              "request_key":job["request_key"],"provider":job["provider"],"model":job["model"],
              "model_alias":job["model_alias"],"project_id":job["project_id"],
              "project":(inputs.get("project_context") or {}).get("project"),
              "workflow_project":project["workflow_project"] if project else None,
              "inputs":inputs,"reserved_cost":job["reserved_cost"],
              "quote_source":PRICE_SOURCE if job["capability"]=="video" else IMAGE_PRICE_SOURCE if job["capability"]=="image" else None,
              "local_job_id":job["local_job_id"],"local_media_id":job["local_media_id"],
              "provider_job_id":job["provider_job_id"]}
        store.audit(c,"local-connector","ai_studio.claim",job["task_id"],{"artifact_id":artifact_id})
        return spec


def _video_stage_path(artifact_id: str) -> Path:
    return store.DATA / "connector_staging" / "ai_studio" / (artifact_id + ".mp4")


def video_upload_state(artifact_id: str, sha256: str) -> dict:
    if not re.fullmatch(r"aia_[a-f0-9]{16}", artifact_id) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("AI 视频编号或校验值无效")
    with store.connect() as c:
        row=c.execute("SELECT capability,status,file_ref FROM ai_studio_artifacts WHERE id=?",(artifact_id,)).fetchone()
        if row is None:raise KeyError(artifact_id)
        if row["capability"]!="video":raise ValueError("该创作任务不是视频")
        status=row["status"]
        result=Path(row["file_ref"]) if row["file_ref"] else None
    if status=="completed":
        result_dir=(store.DATA/"ai_studio"/"results").resolve()
        if (result is None or result.is_symlink() or not result.is_file() or
                result.resolve().parent!=result_dir or result.name!=artifact_id+".mp4" or
                store.digest_file(result)!=sha256):
            raise ValueError("该任务已有不同的视频成果")
        return {"status":"already_completed","sha256":sha256,"size":result.stat().st_size}
    if status not in {"dispatching","submitted","running","download_pending","unknown_submission"}:
        raise ValueError("该任务状态不允许回传视频")
    staged=_video_stage_path(artifact_id)
    if staged.exists():
        if (staged.is_symlink() or not staged.is_file() or
                not 0<staged.stat().st_size<=VIDEO_FILE_LIMIT or store.digest_file(staged)!=sha256):
            raise ValueError("该任务已有不同的暂存视频")
        return {"status":"already_staged","sha256":sha256,"size":staged.stat().st_size}
    return {"status":"ready","sha256":sha256}


def stage_video_file(artifact_id: str, uploaded: Path, sha256: str) -> dict:
    """Keep one immutable video file per AI Studio job for safe report retries."""
    with VIDEO_UPLOAD_LOCK:
        state=video_upload_state(artifact_id,sha256)
        if state["status"]!="ready":return state
        uploaded=Path(uploaded)
        stage_dir=store.DATA/"connector_staging"/"ai_studio"
        if uploaded.is_symlink() or not uploaded.is_file() or uploaded.resolve().parent!=stage_dir.resolve():
            raise ValueError("AI 视频暂存路径无效")
        size=uploaded.stat().st_size
        if not 0<size<=VIDEO_FILE_LIMIT or store.digest_file(uploaded)!=sha256:
            raise ValueError("AI 视频大小或校验值无效")
        with uploaded.open("rb") as stream:
            if b"ftyp" not in stream.read(32):raise ValueError("AI 视频结果不是可识别 MP4")
        target=_video_stage_path(artifact_id)
        try:os.link(uploaded,target)
        except FileExistsError:return video_upload_state(artifact_id,sha256)
        return {"status":"staged","sha256":sha256,"size":size}


def connector_report(artifact_id: str, report: dict) -> dict:
    if not isinstance(report,dict): raise ValueError("执行回报无效")
    state=str(report.get("status") or "")
    if state not in {"submitted","running","download_pending","completed","failed","unknown_submission"}:
        raise ValueError("执行状态无效")
    with store.connect() as c:
        row=c.execute("SELECT * FROM ai_studio_artifacts WHERE id=?",(artifact_id,)).fetchone()
        if row is None: raise KeyError(artifact_id)
        job=dict(row)
        if job["status"]=="completed":
            actual=report.get("actual_cost")
            if job["capability"]=="image" and state=="completed" and job["actual_cost"] is None and actual is not None:
                source=report.get("billing_source")
                if (isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual) or actual<0 or
                    not isinstance(source,str) or not source.strip() or len(source)>400 or
                    report.get("local_media_id")!=job["local_media_id"] or
                    report.get("provider_job_id")!=job["provider_job_id"]):
                    raise ValueError("图片费用补报缺少已核对账单或原任务编号")
                c.execute("UPDATE ai_studio_artifacts SET actual_cost=?,updated_at=? WHERE id=?",
                          (actual,store.now(),artifact_id))
                store.audit(c,"local-connector","ai_studio.image_billing",job["task_id"],
                            {"artifact_id":artifact_id,"media_id":job["local_media_id"],
                             "source":source[:400],"actual_cost":actual})
            return {"id":artifact_id,"status":"completed"}
        if job["status"]=="failed": return {"id":artifact_id,"status":"failed"}
        if job["status"]=="unknown_submission" and state not in {"submitted","running","download_pending","completed","unknown_submission"}:
            raise ValueError("未知提交不能直接重试")
        local_job=str(report.get("local_job_id") or job["local_job_id"] or "")[:100] or None
        local_media=str(report.get("local_media_id") or job["local_media_id"] or "")[:100] or None
        provider_job=str(report.get("provider_job_id") or job["provider_job_id"] or "")[:150] or None
        if job["local_media_id"] and local_media!=job["local_media_id"]:raise ValueError("原媒体任务编号不能替换")
        if job["provider_job_id"] and provider_job!=job["provider_job_id"]:raise ValueError("原供应商任务编号不能替换")
        actual=report.get("actual_cost")
        if actual is not None:
            if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not math.isfinite(actual) or actual<0:
                raise ValueError("实际费用格式无效")
            if job["capability"]=="image" and (not isinstance(report.get("billing_source"),str) or not report["billing_source"].strip()):
                raise ValueError("图片实际费用需要已核对账单来源")
        if state in {"submitted","running","download_pending","unknown_submission","failed"}:
            error=_safe_error(report.get("error"))
            c.execute("""UPDATE ai_studio_artifacts SET status=?,local_job_id=?,local_media_id=?,
                      provider_job_id=?,actual_cost=COALESCE(?,actual_cost),error=?,updated_at=? WHERE id=?""",
                      (state,local_job,local_media,provider_job,actual,error,store.now(),artifact_id))
            store.audit(c,"local-connector","ai_studio."+state,job["task_id"],{"artifact_id":artifact_id})
            return {"id":artifact_id,"status":state}
        usage=report.get("usage")
        if usage is not None and not isinstance(usage,dict): raise ValueError("用量格式无效")
        if job["capability"] in {"video","image"}:
            if not local_media or not provider_job: raise ValueError("媒体结果缺少原任务证据")
            is_image=job["capability"]=="image"
            if is_image and (job["provider"]!="wanjie" or job["model"]!=IMAGE_MODEL or report.get("model")!=IMAGE_MODEL):
                raise ValueError("图片回报不是指定万界即梦模型")
            streamed_sha=report.get("video_sha256") if not is_image else None
            staged=None
            if streamed_sha is not None:
                if not isinstance(streamed_sha,str) or not re.fullmatch(r"[a-f0-9]{64}",streamed_sha):
                    raise ValueError("AI 视频校验值无效")
                staged=_video_stage_path(artifact_id)
                if (not staged.is_file() or staged.is_symlink() or
                        not 0<staged.stat().st_size<=VIDEO_FILE_LIMIT or
                        store.digest_file(staged)!=streamed_sha):
                    raise ValueError("已生成的视频尚未完整回传")
                with staged.open("rb") as stream:
                    if b"ftyp" not in stream.read(32):raise ValueError("AI 视频结果不是可识别 MP4")
                media_bytes=None
                suffix=".mp4"
            else:
                limit=MAX_IMAGE if is_image else MAX_VIDEO
                encoded=report.get("image_base64" if is_image else "video_base64")
                if not isinstance(encoded,str) or len(encoded)>limit*4//3+8: raise ValueError("媒体回收数据无效")
                try: media_bytes=base64.b64decode(encoded,validate=True)
                except ValueError as exc: raise ValueError("媒体编码无效") from exc
                if not media_bytes or len(media_bytes)>limit: raise ValueError("媒体结果为空或超出大小限制")
            if is_image:
                if media_bytes.startswith(b"\x89PNG\r\n\x1a\n"):suffix=".png"
                elif media_bytes.startswith(b"\xff\xd8\xff"):suffix=".jpg"
                elif media_bytes.startswith(b"RIFF") and media_bytes[8:12]==b"WEBP":suffix=".webp"
                else:raise ValueError("图片结果不是可识别的 PNG/JPEG/WebP")
            elif staged is None:
                if b"ftyp" not in media_bytes[:32]: raise ValueError("视频结果不是有效 MP4")
                suffix=".mp4"
            result_dir=store.DATA/"ai_studio"/"results";result_dir.mkdir(parents=True,exist_ok=True)
            target=result_dir/(artifact_id+suffix)
            if not target.exists():
                if staged is not None:
                    os.link(staged,target)
                else:
                    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,"wb") as stream:stream.write(media_bytes);stream.flush();os.fsync(stream.fileno())
            elif store.digest_file(target)!=(streamed_sha or hashlib.sha256(media_bytes).hexdigest()):
                raise ValueError("同一任务已有不同媒体结果")
            media_url="/api/ai-studio/artifacts/"+artifact_id+"/download"
            output={"images":[{"preview_url":media_url,"download_url":media_url,
                               "size":len(media_bytes),"format":suffix.lstrip(".")}] } if is_image else {
                    "videos":[{"preview_url":media_url,"download_url":media_url,
                               "size":staged.stat().st_size if staged is not None else len(media_bytes),
                               "technical":report.get("technical") or {}}]}
            file_ref=str(target)
        else:
            if job["model"]!="deepseek-v4.1-flash" or report.get("model")!="deepseek-v4.1-flash":
                raise ValueError("文本回报不是指定 DeepSeek 模型")
            output=report.get("output")
            if not isinstance(output,dict) or len(store.dumps(output))>120_000:
                raise ValueError("文本成果结构无效或过长")
            file_ref=None
        c.execute("""UPDATE ai_studio_artifacts SET status='completed',output=?,usage=?,actual_cost=?,
                   provider_job_id=?,local_job_id=?,local_media_id=?,file_ref=?,error=NULL,
                   completed_at=?,updated_at=? WHERE id=?""",
                  (store.dumps(output),store.dumps(usage) if usage is not None else None,actual,
                   provider_job,local_job,local_media,file_ref,store.now(),store.now(),artifact_id))
        store.audit(c,"local-connector","ai_studio.complete",job["task_id"],
                    {"artifact_id":artifact_id,"provider":job["provider"],"model":job["model"]})
        return {"id":artifact_id,"status":"completed"}


def _artifact_bytes(item: dict) -> tuple[str,bytes]:
    if item["status"]!="completed": raise ValueError("成果尚未完成")
    if item["type"] in {"video","image"}:
        path=Path(item["file_ref"]).resolve(strict=True)
        if not path.is_relative_to((store.DATA/"ai_studio"/"results").resolve()): raise PermissionError("成果文件路径无效")
        return item["id"]+path.suffix,path.read_bytes()
    output=store.parse(item["output"],{})
    text=output.get("text") if isinstance(output,dict) else None
    if not isinstance(text,str): text=json.dumps(output,ensure_ascii=False,indent=2)
    return item["id"]+".md",text.encode("utf-8")


def save_to_project(user: dict, artifact_id: str, data: dict) -> dict:
    with SAVE_LOCK:
        return _save_to_project_unlocked(user,artifact_id,data)


def _save_to_project_unlocked(user: dict, artifact_id: str, data: dict) -> dict:
    project_id=str((data or {}).get("project_id") or "")
    with store.connect() as c:
        _project(c,user,project_id)
        item=_artifact(c,user,artifact_id)
        if item["project_id"] and item["project_id"]!=project_id: raise PermissionError("成果已绑定其他项目")
        if item["asset_id"]:
            return {"artifact":_public(item,user,True),"asset_id":item["asset_id"]}
    if item["type"]=="video":
        if item["status"]!="completed":raise ValueError("成果尚未完成")
        asset=store.register_ai_video_file(project_id,Path(item["file_ref"]),user["id"])
    else:
        filename,contents=_artifact_bytes(item)
        asset=store.register_submission_asset(project_id,filename,contents,user["id"])
    with store.connect() as c:
        fresh=_artifact(c,user,artifact_id)
        if fresh["asset_id"]:
            # A concurrent save produced another asset; keep the first link.
            return {"artifact":_public(fresh,user,True),"asset_id":fresh["asset_id"]}
        c.execute("UPDATE ai_studio_artifacts SET project_id=?,asset_id=?,updated_at=? WHERE id=?",
                  (project_id,asset["id"],store.now(),artifact_id))
        store.audit(c,user["id"],"ai_studio.save",fresh["task_id"],{"artifact_id":artifact_id,"asset_id":asset["id"]})
        row=dict(c.execute("SELECT * FROM ai_studio_artifacts WHERE id=?",(artifact_id,)).fetchone())
        return {"artifact":_public(row,user,True),"asset_id":asset["id"]}


def turn_into_task(user: dict, artifact_id: str, data: dict) -> dict:
    title=_text_field((data or {}).get("title"),"任务名称",180)
    if not title: raise ValueError("请填写任务名称")
    with store.connect() as c:
        item=_artifact(c,user,artifact_id)
        project_id=item["project_id"]
        if not project_id: raise ValueError("先将成果保存到项目")
        _project(c,user,project_id)
        if item["status"]!="completed": raise ValueError("成果尚未完成")
        existing=c.execute("SELECT task_id FROM ai_studio_task_links WHERE artifact_id=?",(artifact_id,)).fetchone()
        if existing:return {"task_id":existing[0],"status":"existing"}
        asset_id=item["asset_id"]
    if not asset_id:
        saved=save_to_project(user,artifact_id,{"project_id":project_id})
        asset_id=saved["asset_id"]
    with store.connect() as c:
        existing=c.execute("SELECT task_id FROM ai_studio_task_links WHERE artifact_id=?",(artifact_id,)).fetchone()
        if existing:return {"task_id":existing[0],"status":"existing"}
        tid="t_"+secrets.token_hex(8)
        stamp=store.now()
        c.execute("""INSERT INTO tasks(id,project_id,workflow_id,title,why,assignee_id,priority,status,
                   context,input_assets,instructions,deliverable_contract,qc_contract,budget_cap,
                   created_at,updated_at,created_by)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (tid,project_id,"WF-AI",title,"基于 AI 创作成果继续制作",user["id"] if user["role"]=="employee" else None,
                  2,"assigned" if user["role"]=="employee" else "ready",
                   store.dumps({"source_ai_artifact":artifact_id}),store.dumps([asset_id]),"[]",
                   "{}","{}",0,stamp,stamp,user["id"]))
        c.execute("INSERT INTO ai_studio_task_links(artifact_id,task_id) VALUES(?,?)",(artifact_id,tid))
        store.audit(c,user["id"],"ai_studio.task",tid,{"artifact_id":artifact_id,"asset_id":asset_id})
        return {"task_id":tid,"status":"assigned" if user["role"]=="employee" else "ready"}


def continue_artifact(user: dict, artifact_id: str, data: dict) -> dict:
    with store.connect() as c:
        item=_artifact(c,user,artifact_id)
        if item["status"]!="completed" or item["capability"] in {"video","image"}:
            raise ValueError("当前成果不能继续编辑；图片或视频请按新需求重新生成")
        old=store.parse(item["inputs"],{})
        previous=store.parse(item["output"],{})
    instruction=_text_field((data or {}).get("prompt") or (data or {}).get("instruction"),"续写要求",6000)
    if not instruction: raise ValueError("请写明继续编辑的要求")
    content=previous.get("text") if isinstance(previous,dict) else None
    if not isinstance(content,str): content=store.dumps(previous)
    spec={"capability":item["capability"],"project_id":item["project_id"],"task_id":item["task_id"],
          "parent_id":artifact_id,"scenario":old.get("scenario"),"language":old.get("language","zh"),
          "prompt":"基于已有成果继续处理。\n已有成果：\n"+content[:9000]+"\n新要求：\n"+instruction}
    return create_run(user,spec)


def set_budget(user: dict, data: dict) -> dict:
    if user["role"] not in {"founder","manager"}: raise PermissionError("只有管理者可调整 AI 预算")
    scope=str(data.get("scope") or "")
    scope_id=str(data.get("scope_id") or "")
    cap=data.get("cap_cny")
    if scope not in {"project","user","task"} or not scope_id or isinstance(cap,bool) or not isinstance(cap,(int,float)) or not math.isfinite(cap) or cap<0 or cap>10000:
        raise ValueError("预算设置无效")
    with store.connect() as c:
        table={"project":"projects","user":"users","task":"tasks"}[scope]
        if not c.execute("SELECT 1 FROM "+table+" WHERE id=?",(scope_id,)).fetchone():raise KeyError(scope_id)
        c.execute("""INSERT INTO ai_studio_budgets(scope,scope_id,cap_cny,updated_at,updated_by)
                   VALUES(?,?,?,?,?) ON CONFLICT(scope,scope_id) DO UPDATE SET
                   cap_cny=excluded.cap_cny,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
                  (scope,scope_id,float(cap),store.now(),user["id"]))
        if scope=="task":
            c.execute("UPDATE tasks SET budget_cap=?,updated_at=? WHERE id=?",(float(cap),store.now(),scope_id))
        store.audit(c,user["id"],"ai_studio.budget",scope_id if scope=="task" else None,
                    {"scope":scope,"scope_id":scope_id,"cap_cny":float(cap)})
        return {"scope":scope,"scope_id":scope_id,"cap_cny":float(cap)}


def set_route(user: dict, data: dict) -> dict:
    """Configure only providers with a validated executor; no silent fallback."""
    if user["role"] not in {"founder","manager"}: raise PermissionError("只有管理者可调整 AI 路由")
    business=str(data.get("business_model") or "")
    provider=data.get("primary_provider")
    enabled=data.get("enabled")
    approved={"text":"wanjie","sd2.0":"runy","sd2.5":"runy","image":"wanjie"}
    if business not in approved or not isinstance(enabled,bool):
        raise ValueError("业务路由或启停状态无效")
    if provider!=approved[business] and not (business=="image" and not enabled and provider in {None,""}):
        raise ValueError("目标 Provider 尚未通过该业务模型的生产级验证")
    if business=="image" and enabled and data.get("model") not in {None,IMAGE_MODEL}:
        raise ValueError("图片路由只允许已验证的即梦 4.0 模型")
    if business=="image" and not enabled:provider=None
    with store.connect() as c:
        if enabled and business in VIDEO_PRICES:
            # _route also reads policy; check the independent connector snapshot.
            row=c.execute("SELECT provider,model,checked_at FROM martial_routes WHERE model_alias=?",(business,)).fetchone() if c.execute("SELECT 1 FROM sqlite_master WHERE name='martial_routes'").fetchone() else None
            if not row or row["provider"]!="runy" or row["model"]!=VIDEO_PRICES[business][0]:
                raise ValueError("当前没有已验证的润元模型路由快照")
        if enabled and business=="text" and not _has_recent_text_success(c):
            raise ValueError("万界 DeepSeek 尚无本系统成功调用证据")
        c.execute("""UPDATE ai_studio_routes SET primary_provider=?,enabled=?,updated_at=?,updated_by=?
                   WHERE business_model=?""",(provider,1 if enabled else 0,store.now(),user["id"],business))
        store.audit(c,user["id"],"ai_studio.route",None,
                    {"business_model":business,"primary_provider":provider,"enabled":enabled})
        return {"business_model":business,"primary_provider":provider,"enabled":enabled}


def admin(user: dict) -> dict:
    if user["role"] not in {"founder","manager"}: raise PermissionError("没有 AI 能力管理权限")
    with store.connect() as c:
        route20=_route(c,"sd2.0");route25=_route(c,"sd2.5")
        text_ready=_has_recent_text_success(c)
        image_enabled=_image_enabled(c)
        image_ready=image_enabled and _has_image_success(c)
        recent=[dict(r) for r in c.execute("""SELECT created_at,provider,model,capability,status,usage,actual_cost,reserved_cost,error
                                               FROM ai_studio_artifacts ORDER BY created_at DESC LIMIT 30""")]
        calls=[]
        for row in recent:
            usage=store.parse(row.pop("usage"),{}) or {}
            row["input_tokens"]=usage.get("prompt_tokens") or usage.get("input_tokens")
            row["output_tokens"]=usage.get("completion_tokens") or usage.get("output_tokens")
            calls.append(row)
        historical=[dict(r) for r in c.execute("""SELECT created_at,provider,model,purpose capability,usage,actual_cost
                                                   FROM costs ORDER BY created_at DESC LIMIT 10""")]
        for row in historical:
            usage=store.parse(row.pop("usage"),{}) or {}
            row.update(status="completed",input_tokens=usage.get("prompt_tokens") or usage.get("input_tokens"),
                       output_tokens=usage.get("completion_tokens") or usage.get("output_tokens"),error=None)
            calls.append(row)
        calls.sort(key=lambda x:x["created_at"],reverse=True)
        budgets=[dict(r) for r in c.execute("SELECT scope,scope_id,cap_cny,updated_at FROM ai_studio_budgets ORDER BY updated_at DESC LIMIT 50")]
        policies={r["business_model"]:dict(r) for r in c.execute("SELECT business_model,primary_provider,enabled,updated_at FROM ai_studio_routes")}
        def route_info(key,label,status,secondary=None):
            policy=policies[key]
            return {"business_model":label,"route_key":key,
                    "primary":policy["primary_provider"] if policy["enabled"] else None,
                    "secondary":secondary,"enabled":bool(policy["enabled"]),
                    "status":status if policy["enabled"] else "DISABLED",
                    "updated_at":policy["updated_at"]}
        def provider_activity(provider_name):
            rows=[r for r in recent if r["provider"]==provider_name]
            if not rows:return {"last_call":None,"last_error":None,"error_rate":None,
                                "known_cost_cny":None,"unpriced_calls":0}
            errors=[r for r in rows if r["status"] in {"failed","unknown_submission"}]
            priced=[r["actual_cost"] for r in rows if r["actual_cost"] is not None]
            return {"last_call":rows[0]["created_at"],"last_error":errors[0]["error"] if errors else None,
                    "error_rate":round(len(errors)/len(rows),3),
                    "known_cost_cny":round(sum(priced),4) if priced else None,
                    "unpriced_calls":len(rows)-len(priced)}
        wanjie_activity=provider_activity("wanjie")
        runy_activity=provider_activity("runy")
        return {
            "providers":[
                {"name":"万界","status":"CONNECTED" if wanjie_activity["last_call"] and text_ready else "TESTED" if text_ready else "DISCOVERED",
                 "endpoint_profile":"公司工作流本机连接器 / OpenAI Chat Completions","secret_ref":"本机公司工作流私有凭据（值不上传）",
                 "last_check":wanjie_activity["last_call"],**wanjie_activity},
                {"name":"润元","status":"CONNECTED" if route20 or route25 else "TESTED",
                 "endpoint_profile":"公司工作流本机连接器 / 视频任务","secret_ref":"本机公司工作流私有凭据（值不上传）",
                 "last_check":(route25 or route20 or {}).get("checked_at"),**runy_activity},
                {"name":"OpenAI","status":"DISABLED","endpoint_profile":"未接入 AI 创作中心","secret_ref":None,"last_check":None,"last_error":None},
            ],
            "models":[
                {"model":"deepseek-v4.1-flash","provider":"万界","capability":"文本","status":"PRODUCTION_READY" if text_ready else "TESTED","input_modes":"text"},
                {"model":IMAGE_MODEL,"provider":"万界","capability":"图片","status":"PRODUCTION_READY" if image_ready else "TESTED",
                 "input_modes":"单张文生图；图片参考未接通"},
                {"model":"CosyVoice","provider":"万界","capability":"TTS","status":"PARTIAL","input_modes":"历史成功；最近服务 HTTP 500，待恢复验证"},
                {"model":"gemini-2.5-flash-lite","provider":"万界","capability":"音频理解","status":"TESTED","input_modes":"仅分析输入音频，不能生成 BGM 或音效"},
                {"model":"doubao-seedance-2.0","provider":"润元","capability":"视频","status":"PRODUCTION_READY" if route20 else "TESTED","input_modes":"已验证武学参考；AI 创作文生视频待单独验收"},
                {"model":"doubao-seedance-2-5","provider":"润元","capability":"视频","status":"PRODUCTION_READY" if route25 else "TESTED","input_modes":"已验证武学参考；AI 创作文生视频待单独验收"},
                {"model":"doubao-seedance-2-5-260628","provider":"万界","capability":"视频","status":"PARTIAL","input_modes":"已发现；真人视频参考与回收未形成生产级验证"},
            ],
            "capability_registry":{
                "wanjie":{"text":"PRODUCTION_READY" if text_ready else "TESTED",
                           "image":"PRODUCTION_READY" if image_ready else "TESTED",
                           "video":"TESTED","tts":"PARTIAL","audio":"DISABLED","music":"DISABLED"},
                "runy":{"text":"DISCOVERED","image":"DISABLED",
                        "video":"PRODUCTION_READY" if route20 or route25 else "TESTED",
                        "tts":"DISABLED","audio":"DISABLED","music":"DISABLED"}},
            "routes":[
                route_info("text","文本创作/分析","PRODUCTION_READY" if text_ready else "TESTED"),
                route_info("sd2.0","Seedance 2.0","PRODUCTION_READY" if route20 else "TESTED"),
                route_info("sd2.5","Seedance 2.5","PRODUCTION_READY" if route25 else "TESTED",
                           "万界（PARTIAL，自动切换关闭）"),
                route_info("image","图片生成","PRODUCTION_READY" if image_ready else "CONFIGURED"),
            ],
            "calls":calls[:40],"budgets":budgets,
            "defaults":{"project_cap_cny":DEFAULT_PROJECT_CAP,"user_cap_cny":DEFAULT_USER_CAP,
                        "video_quote_source":PRICE_SOURCE},
        }

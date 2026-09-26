"""Thin adapters to the installed Company Workflow and existing media studio."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import store

WORKFLOW_ROOT = Path("/Users/majorchen/Documents/Codex/2026-09-18/new-chat/outputs/company-workflow")
CLIENT = WORKFLOW_ROOT / "app" / "client.py"
MEDIA_STUDIO_URL = "https://ai.duodianqian.cn/"
REQUIRED_PREPARED = {"context_summary", "teaching_structure", "script", "shot_plan", "generation_prompt", "negative_constraints", "reference_mapping", "qc_checklist", "missing_inputs"}


def submit_copilot(task_id: str, user: dict, category: str, question: str) -> dict:
    if os.environ.get("YOODUN_CONNECTOR_MODE") != "remote":
        raise RuntimeError("任务助手连接器未启用")
    message=store.create_copilot_message(task_id,user,category,question)
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        project=store.record(c,"projects",task["project_id"])
        workflow=store.record(c,"workflows",task["workflow_id"])
        assets=[{"name":r["name"],"type":r["type"],"version":r["version"]} for r in c.execute("SELECT a.name,a.type,a.version FROM assets a JOIN json_each(?) j ON a.id=j.value",(task["input_assets"],))]
        feedback=[{"problem":r["problem"],"change_request":r["change_request"],"preserve":r["preserve"]} for r in c.execute("SELECT problem,change_request,preserve FROM feedback WHERE task_id=? ORDER BY created_at DESC LIMIT 3",(task_id,))]
        knowledge=[{"scope":r["scope"],"version":r["version"],"source":r["source"]} for r in c.execute("SELECT scope,version,source FROM knowledge WHERE status='active' LIMIT 20")]
    context={"task":task["title"],"why":task["why"],"project":project["name"],"workflow":workflow["name"],"status":task["status"],"assets":assets,"character_lock":store.parse(task["character_lock"],{}),"motion_lock":store.parse(task["motion_lock"],{}),"prepared":store.parse(task["ai_prepared"],{}),"qc_contract":store.parse(task["qc_contract"],{}),"revision_feedback":feedback,"active_knowledge_register":knowledge}
    brief="任务内员工求助。只按已给事实回答；未看到的图片/视频不得声称已检查。\n上下文："+json.dumps(context,ensure_ascii=False)[:12000]+"\n问题类型："+category+"\n员工问题："+question+"\n只返回 JSON：answer（简短回答）,next_action（员工下一步具体动作）,needs_manager（布尔值）,escalation_reason（若需管理者决定则写原因）。不得更改锁定、预算、知识、任务状态或替员工验收。"
    jid="aj_"+store.secrets.token_hex(8)
    spec={"request_key":"workos:copilot:"+message["id"],"project":project["workflow_project"],"units":[{"brief":brief}]}
    requests_dir=store.DATA/"requests";requests_dir.mkdir(parents=True,exist_ok=True)
    path=requests_dir/(jid+".json")
    path.write_text(json.dumps(spec,ensure_ascii=False));path.chmod(0o600)
    with store.connect() as c:
        c.execute("INSERT INTO ai_jobs(id,task_id,kind,request_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(jid,task_id,"copilot",spec["request_key"],"queued",store.now(),store.now()))
        c.execute("UPDATE copilot_messages SET ai_job_id=? WHERE id=?",(jid,message["id"]))
        store.audit(c,user["id"],"copilot.queued",task_id,{"message_id":message["id"],"job_id":jid})
    return message


def _client(*args: str) -> dict:
    if not CLIENT.is_file():
        raise RuntimeError("Company Workflow 客户端不存在")
    result = subprocess.run([sys.executable, str(CLIENT), *args], cwd=str(WORKFLOW_ROOT), capture_output=True, text=True, timeout=50, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "Company Workflow 调用失败")[-500:])
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("Company Workflow 返回内容不是 JSON") from exc


def _brief(task: dict, assets: list[dict], kind: str, delivery: dict | None = None) -> str:
    context = store.parse(task["context"], {})
    char = store.parse(task.get("character_lock"), {})
    motion = store.parse(task.get("motion_lock"), {})
    lines = [
        f"Work OS 任务编号：{task['id']}", f"项目：{task['project_id']}；流程：{task['workflow_id']}",
        f"员工业务任务：{task['title']}", f"业务目的：{task['why']}",
        "已核实上下文（不得凭历史候选改写当前事实）："+json.dumps(context,ensure_ascii=False)[:900],
        "已登记资产："+json.dumps([{"asset_id":a["id"],"name":a["name"],"type":a["type"],"sha256":a["sha256"],"version":a["version"]} for a in assets],ensure_ascii=False)[:950],
    ]
    if char: lines.append("角色锁定："+json.dumps(char,ensure_ascii=False)[:400])
    if motion: lines.append("真人动作参考锁定："+json.dumps(motion,ensure_ascii=False)[:650])
    for asset in assets[:5]:
        path=Path(asset["storage_ref"])
        if asset["type"] not in {"document","script","prompt"} or path.suffix.lower() not in {".md",".txt"} or not path.is_file() or path.stat().st_size>30000:
            continue
        excerpt=path.read_text(encoding="utf-8",errors="replace")[:1000]
        if re.search(r"(?:API[_-]?KEY|AUTH[_-]?TOKEN|PASSWORD|SECRET)\s*[:=]",excerpt,re.I):
            lines.append("素材正文因疑似凭据已略去："+asset["name"])
        else:
            lines.append("已登记文本素材片段（"+asset["name"]+"）：\n"+excerpt)
    if kind == "prepare":
        lines += [
            "请准备员工可直接执行的工作包。不要声称已观看未提供的视频，不把旧角色或旧产品规则当现行规则。",
            "图片和视频文件已经随任务供员工下载；模型暂不能目视它们并非素材缺失。须在检查清单写明人工对照，不因此把任务阻塞。",
            "交付 prepared.json，必须是单个 JSON 对象，字段：context_summary（短文本）, teaching_structure（步骤数组）, script（短文本）, shot_plan（镜头或操作步骤数组）, generation_prompt（员工可复制到指定工具的业务提示词；不是系统提示词）, negative_constraints（数组）, reference_mapping（数组）, qc_checklist（数组）, missing_inputs（数组）。",
            "若关键输入缺失，在 missing_inputs 写清具体文件／事实；其余字段仍保留结构，不能虚构。尽量短且可执行。",
        ]
    else:
        lines += [
            "这是仅依据任务文字和文件元数据的 AI 初检，未向你提供视频画面；不得声称已看过视频或完成动作／艺术验收。",
            "当前员工交付："+json.dumps(delivery or {},ensure_ascii=False)[:3500],
            "交付 precheck.json，单个 JSON 对象，字段 verdict（pass/fail/unverified 之一）, issues（数组）, limitations（数组）, recommended_human_checks（数组）。视频画面／动作／角色一致性不可从元数据判断时 verdict 必须为 unverified。",
        ]
    return "\n".join(lines)[:4800]


def _sources(task: dict, assets: list[dict]) -> list[dict]:
    project_root = store.PROJECT_ROOTS[task["project_id"]].resolve()
    refs = []
    for asset in assets:
        if asset["type"] not in {"document","script","prompt"}:
            continue
        path = Path(asset["storage_ref"])
        if not path.is_file() or path.suffix.lower() not in {".md",".txt"}:
            continue
        try: relative = path.resolve().relative_to(project_root)
        except ValueError: continue
        if path.stat().st_size > 100000:
            continue
        refs.append({"path":str(relative),"offset":0,"limit":min(path.stat().st_size,3500)})
        if len(refs)>=2: break
    return refs


def submit_ai_job(task_id: str, kind: str, actor: str) -> dict:
    if kind not in {"prepare","precheck"}:
        raise ValueError("无效 AI 任务类型")
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        if kind=="prepare" and task["status"] not in {"planned","blocked"}:
            raise ValueError("当前任务不能重复准备")
        if kind=="precheck" and task["status"]!="technical_checked":
            raise ValueError("需要先完成技术检查")
        existing=c.execute("SELECT * FROM ai_jobs WHERE task_id=? AND kind=? AND status IN ('queued','dispatching','submitted','running','complete','unknown_submission') ORDER BY created_at DESC LIMIT 1",(task_id,kind)).fetchone()
        if existing:
            prior=store.parse(existing["result"],{})
            retry_missing=(kind=="prepare" and existing["status"]=="complete" and task["status"]=="blocked" and bool((prior.get("body") or {}).get("missing_inputs")))
            if not retry_missing and (kind=="prepare" or task["revision"]==prior.get("revision",-1)):
                return dict(existing)
        attempt=c.execute("SELECT COUNT(*) FROM ai_jobs WHERE task_id=? AND kind=?",(task_id,kind)).fetchone()[0]+1
        assets=[dict(r) for r in c.execute("SELECT a.* FROM assets a JOIN json_each(?) j ON a.id=j.value",(task["input_assets"],))]
        delivery=dict(store.latest_delivery(c,task_id)) if kind=="precheck" else None
        project=store.record(c,"projects",task["project_id"])
    request_key=f"workos:{task_id}:{kind}:{task['revision']}"+(f":a{attempt}" if attempt>1 else "")
    filename="prepared.json" if kind=="prepare" else "precheck.json"
    spec={
        "request_key":request_key,"title":f"Work OS {kind} {task['title']}","project":project["workflow_project"],
        "kind":"design" if kind=="prepare" else "research",
        "units":[{"title":kind,"brief":_brief(task,assets,kind,delivery),"sources":_sources(task,assets) if kind=="prepare" else []}],
        "delivery_contract":{"required_files":[filename],"acceptance_criteria":["JSON可解析，字段齐全；不虚构视频画面或历史事实","明确缺失输入与人工验收边界"]},
        "max_steps":3,"max_output_tokens":3000 if kind=="prepare" else 1500,"max_model_calls":5 if kind=="prepare" else 3,"max_revisions":1,
    }
    requests_dir=store.DATA/"requests"
    requests_dir.mkdir(parents=True,exist_ok=True)
    spec_path=requests_dir/(task_id+"-"+kind+"-"+str(task["revision"])+".json")
    spec_path.write_text(json.dumps(spec,ensure_ascii=False,indent=2))
    spec_path.chmod(0o600)
    if os.environ.get("YOODUN_AI_ROUTE") == "direct" and os.environ.get("YOODUN_CONNECTOR_MODE") != "remote":
        from direct_deepseek import analyze
        with store.connect() as c:
            jid="aj_"+store.secrets.token_hex(8)
            c.execute("INSERT INTO ai_jobs(id,task_id,kind,request_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(jid,task_id,kind,request_key,"dispatching",store.now(),store.now()))
            store.audit(c,actor,"ai."+kind+".direct_submit",task_id,{"request_key":request_key,"model":"deepseek-v4.1-flash"})
        try:report=analyze(spec,kind)
        except Exception as exc:
            state="unknown_submission" if type(exc).__name__=="UnknownSubmission" else "failed"
            connector_report(jid,{"status":state,"error":str(exc)[:300]})
            raise
        connector_report(jid,report)
        with store.connect() as c:return dict(c.execute("SELECT * FROM ai_jobs WHERE id=?",(jid,)).fetchone())
    if os.environ.get("YOODUN_CONNECTOR_MODE") == "remote":
        with store.connect() as c:
            jid="aj_"+store.secrets.token_hex(8)
            c.execute("INSERT INTO ai_jobs(id,task_id,kind,request_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(request_key) DO UPDATE SET status='queued',error=NULL,updated_at=excluded.updated_at WHERE ai_jobs.status='failed'",(jid,task_id,kind,request_key,"queued",store.now(),store.now()))
            store.audit(c,actor,"ai."+kind+".queued",task_id,{"request_key":request_key})
            return dict(c.execute("SELECT * FROM ai_jobs WHERE request_key=?",(request_key,)).fetchone())
    try:
        result=_client("plan-create",str(spec_path))
    except Exception as exc:
        with store.connect() as c:
            jid="aj_"+store.secrets.token_hex(8)
            c.execute("INSERT OR IGNORE INTO ai_jobs(id,task_id,kind,request_key,status,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(jid,task_id,kind,request_key,"failed",str(exc)[:500],store.now(),store.now()))
            store.audit(c,actor,"ai."+kind+".failed",task_id,{"error_type":type(exc).__name__})
        raise
    plan_id=result.get("id")
    if not isinstance(plan_id,str):
        raise RuntimeError("Company Workflow 未返回计划编号")
    with store.connect() as c:
        jid="aj_"+store.secrets.token_hex(8)
        c.execute("INSERT INTO ai_jobs(id,task_id,kind,plan_id,request_key,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(request_key) DO UPDATE SET status='submitted',plan_id=excluded.plan_id,error=NULL,updated_at=excluded.updated_at WHERE ai_jobs.status='failed'",(jid,task_id,kind,plan_id,request_key,"submitted",store.now(),store.now()))
        task=store.record(c,"tasks",task_id)
        refs=store.parse(task["execution_job_refs"],[])+[{"kind":kind,"plan_id":plan_id,"request_key":request_key,"revision":task["revision"]}]
        c.execute("UPDATE tasks SET execution_job_refs=?,updated_at=? WHERE id=?",(store.dumps(refs),store.now(),task_id))
        store.audit(c,actor,"ai."+kind+".submit",task_id,{"plan_id":plan_id})
        row=c.execute("SELECT * FROM ai_jobs WHERE request_key=?",(request_key,)).fetchone()
        return dict(row)


def sync_ai_job(task_id: str, kind: str, actor: str) -> dict:
    with store.connect() as c:
        row=c.execute("SELECT * FROM ai_jobs WHERE task_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",(task_id,kind)).fetchone()
        if not row: raise ValueError("没有对应的 AI 任务")
        job=dict(row)
        if job["status"]=="complete": return job
    if os.environ.get("YOODUN_CONNECTOR_MODE") == "remote":
        return job
    if not job["plan_id"]:
        raise RuntimeError(job.get("error") or "计划未提交")
    plan=_client("plan",job["plan_id"])
    status=plan.get("status")
    if status not in {"needs_review","needs_attention","cancelled"}:
        with store.connect() as c:
            c.execute("UPDATE ai_jobs SET status=?,updated_at=? WHERE id=?",("running",store.now(),job["id"]))
        return {"id":job["id"],"status":"running","plan_status":status}
    if status!="needs_review" or not plan.get("final_job"):
        with store.connect() as c:
            c.execute("UPDATE ai_jobs SET status='failed',error=?,updated_at=? WHERE id=?",(str(plan.get("error") or status)[:500],store.now(),job["id"]))
            if kind=="prepare":
                store.update_task_status(c,task_id,{"planned","blocked"},"blocked",actor,"AI 准备未完成；请查看执行错误")
        return {"id":job["id"],"status":"failed","plan_status":status}
    filename="prepared.json" if kind=="prepare" else "precheck.json"
    raw=_client("result",plan["final_job"],"--file",filename)
    try:
        parsed=json.loads(raw["text"])
    except (KeyError,ValueError,TypeError) as exc:
        raise RuntimeError("AI 交付 JSON 不可解析") from exc
    if kind=="prepare":
        if not isinstance(parsed,dict) or not REQUIRED_PREPARED.issubset(parsed) or not isinstance(parsed["missing_inputs"],list):
            raise RuntimeError("AI 准备包字段不完整")
    else:
        if not isinstance(parsed,dict) or parsed.get("verdict") not in {"pass","fail","unverified"} or not isinstance(parsed.get("issues"),list):
            raise RuntimeError("AI 初检字段不完整")
    usage=plan.get("actual") or {}
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        c.execute("UPDATE ai_jobs SET status='complete',final_job_id=?,usage=?,result=?,updated_at=? WHERE id=?",(plan["final_job"],store.dumps(usage),store.dumps({"revision":task["revision"],"body":parsed}),store.now(),job["id"]))
        c.execute("INSERT INTO costs(id,task_id,provider,model,purpose,usage,reserved_cost,actual_cost,provider_job_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",("cost_"+store.secrets.token_hex(8),task_id,"万界","company-workflow:mixed",kind,store.dumps(usage),None,usage.get("cost_cny"),plan["final_job"],store.now()))
        if kind=="prepare":
            c.execute("UPDATE tasks SET ai_prepared=?,updated_at=? WHERE id=?",(store.dumps(parsed),store.now(),task_id))
            draft_allowed=bool(store.parse(task["context"],{}).get("draft_with_open_questions"))
            if parsed["missing_inputs"] and not draft_allowed:
                store.update_task_status(c,task_id,{"planned","blocked"},"blocked",actor,"AI 指出缺少："+"；".join(str(x) for x in parsed["missing_inputs"])[:350])
            else:
                store.update_task_status(c,task_id,{"planned","blocked"},"ready",actor)
        else:
            delivery=store.latest_delivery(c,task_id)
            if task["status"]!="technical_checked": raise ValueError("技术检查状态已改变")
            qid="q_"+store.secrets.token_hex(8)
            c.execute("INSERT INTO qc(id,task_id,deliverable_id,stage,result,criteria,findings,reviewer_id,method,coverage,evidence_refs,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(qid,task_id,delivery["id"],"ai",parsed["verdict"],"[]",store.dumps(parsed["issues"]),None,"DeepSeek text/metadata precheck","text_and_metadata_only",store.dumps([job["plan_id"],plan["final_job"]]),store.now()))
            store.update_task_status(c,task_id,{"technical_checked"},"ai_prechecked",actor)
        store.audit(c,actor,"ai."+kind+".complete",task_id,{"plan_id":job["plan_id"],"final_job":plan["final_job"]})
        return {"id":job["id"],"status":"complete","plan_id":job["plan_id"],"result":parsed,"usage":usage}


def connector_jobs() -> list[dict]:
    with store.connect() as c:
        rows=c.execute("SELECT id,task_id,kind,request_key,status,plan_id FROM ai_jobs WHERE status IN ('queued','dispatching','submitted','running') ORDER BY created_at LIMIT 10").fetchall()
        return [dict(row) for row in rows]


def connector_claim(job_id: str) -> dict:
    with store.connect() as c:
        row=c.execute("SELECT * FROM ai_jobs WHERE id=?",(job_id,)).fetchone()
        if row is None or row["status"] != "queued":
            raise ValueError("任务不可认领")
        revision=row["request_key"].rsplit(":",1)[-1]
        path=store.DATA/"requests"/(job_id+".json" if row["kind"]=="copilot" else row["task_id"]+"-"+row["kind"]+"-"+revision+".json")
        spec=json.loads(path.read_text())
        if spec.get("request_key") != row["request_key"]:
            raise ValueError("AI 请求与任务不一致")
        c.execute("UPDATE ai_jobs SET status='dispatching',updated_at=? WHERE id=?",(store.now(),job_id))
        store.audit(c,"local-connector","ai.claim",row["task_id"],{"job_id":job_id})
        return {"id":job_id,"spec":spec}


def connector_report(job_id: str, report: dict) -> dict:
    state=report.get("status")
    if state not in {"submitted","running","complete","failed","unknown_submission"}:
        raise ValueError("无效执行状态")
    with store.connect() as c:
        row=c.execute("SELECT * FROM ai_jobs WHERE id=?",(job_id,)).fetchone()
        if row is None: raise KeyError(job_id)
        job=dict(row)
        if job["status"] in {"complete","failed","unknown_submission"}:
            return {"id":job_id,"status":job["status"]}
        task=store.record(c,"tasks",job["task_id"])
        plan_id=str(report.get("plan_id") or job.get("plan_id") or "")[:100] or None
        if state in {"submitted","running"}:
            c.execute("UPDATE ai_jobs SET status=?,plan_id=?,updated_at=? WHERE id=?",(state,plan_id,store.now(),job_id))
            return {"id":job_id,"status":state}
        if state in {"failed","unknown_submission"}:
            error=str(report.get("error") or state)[:500]
            c.execute("UPDATE ai_jobs SET status=?,plan_id=?,error=?,updated_at=? WHERE id=?",(state,plan_id,error,store.now(),job_id))
            if job["kind"]=="copilot":
                message=c.execute("SELECT id,user_id FROM copilot_messages WHERE ai_job_id=?",(job_id,)).fetchone()
                c.execute("UPDATE copilot_messages SET status=?,answer=?,needs_manager=1,completed_at=? WHERE ai_job_id=?",(state,"这次回答未完成，已转交管理者处理。",store.now(),job_id))
                if message:
                    c.execute("INSERT INTO escalations(id,task_id,message_id,reason,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)",("esc_"+store.secrets.token_hex(8),job["task_id"],message["id"],"任务助手未能回答："+error[:300],"open",message["user_id"],store.now()))
            if job["kind"]=="prepare" and task["status"] in {"planned","blocked"}:
                store.update_task_status(c,job["task_id"],{"planned","blocked"},"blocked","local-connector","AI 准备未完成："+error[:250])
            store.audit(c,"local-connector","ai."+state,job["task_id"],{"job_id":job_id})
            return {"id":job_id,"status":state}
        body=report.get("body")
        if not isinstance(body,dict): raise ValueError("AI 结果必须是 JSON 对象")
        if job["kind"]=="copilot":
            if not isinstance(body.get("answer"),str) or not isinstance(body.get("next_action"),str) or not isinstance(body.get("needs_manager"),bool):
                raise ValueError("任务助手回答字段不完整")
            if len(body["answer"])>4000 or len(body["next_action"])>1000:
                raise ValueError("任务助手回答过长")
        elif job["kind"]=="prepare":
            if not REQUIRED_PREPARED.issubset(body) or not isinstance(body["missing_inputs"],list):
                raise ValueError("AI 准备包字段不完整")
            if task["status"] not in {"planned","blocked"}: raise ValueError("任务状态已改变")
        else:
            if body.get("verdict") not in {"pass","fail","unverified"} or not isinstance(body.get("issues"),list):
                raise ValueError("AI 初检字段不完整")
            if task["status"]!="technical_checked": raise ValueError("任务状态已改变")
        final_job=str(report.get("final_job") or "")[:100]
        if not plan_id or not final_job: raise ValueError("缺少 Company Workflow 执行证据")
        usage=report.get("usage") or {}
        if not isinstance(usage,dict): raise ValueError("用量格式无效")
        c.execute("UPDATE ai_jobs SET status='complete',plan_id=?,final_job_id=?,usage=?,result=?,updated_at=? WHERE id=?",(plan_id,final_job,store.dumps(usage),store.dumps({"revision":task["revision"],"body":body}),store.now(),job_id))
        c.execute("INSERT INTO costs(id,task_id,provider,model,purpose,usage,reserved_cost,actual_cost,provider_job_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",("cost_"+store.secrets.token_hex(8),job["task_id"],"万界","deepseek-v4.1-flash",job["kind"],store.dumps(usage),None,usage.get("cost_cny"),final_job,store.now()))
        if job["kind"]=="copilot":
            message=c.execute("SELECT * FROM copilot_messages WHERE ai_job_id=?",(job_id,)).fetchone()
            if message is None: raise ValueError("任务助手消息不存在")
            needs_manager=body["needs_manager"] or message["category"]=="budget"
            c.execute("UPDATE copilot_messages SET status='complete',answer=?,next_action=?,needs_manager=?,completed_at=? WHERE id=?",(body["answer"],body["next_action"],1 if needs_manager else 0,store.now(),message["id"]))
            if needs_manager:
                reason=str(body.get("escalation_reason") or body["answer"])[:1000]
                c.execute("INSERT INTO escalations(id,task_id,message_id,reason,status,created_by,created_at) VALUES(?,?,?,?,?,?,?)",("esc_"+store.secrets.token_hex(8),job["task_id"],message["id"],reason,"open",message["user_id"],store.now()))
        elif job["kind"]=="prepare":
            c.execute("UPDATE tasks SET ai_prepared=?,updated_at=? WHERE id=?",(store.dumps(body),store.now(),job["task_id"]))
            draft_allowed=bool(store.parse(task["context"],{}).get("draft_with_open_questions"))
            if body["missing_inputs"] and not draft_allowed:
                store.update_task_status(c,job["task_id"],{"planned","blocked"},"blocked","local-connector","AI 指出缺少："+"；".join(map(str,body["missing_inputs"]))[:350])
            else:
                store.update_task_status(c,job["task_id"],{"planned","blocked"},"ready","local-connector")
        else:
            delivery=store.latest_delivery(c,job["task_id"])
            c.execute("INSERT INTO qc(id,task_id,deliverable_id,stage,result,criteria,findings,reviewer_id,method,coverage,evidence_refs,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",("q_"+store.secrets.token_hex(8),job["task_id"],delivery["id"],"ai",body["verdict"],"[]",store.dumps(body["issues"]),None,"Company Workflow DeepSeek text/metadata","text_and_metadata_only",store.dumps([plan_id,final_job]),store.now()))
            store.update_task_status(c,job["task_id"],{"technical_checked"},"ai_prechecked","local-connector")
        refs=store.parse(task["execution_job_refs"],[])+[{"kind":job["kind"],"plan_id":plan_id,"final_job":final_job,"request_key":job["request_key"],"revision":task["revision"]}]
        c.execute("UPDATE tasks SET execution_job_refs=? WHERE id=?",(store.dumps(refs),job["task_id"]))
        store.audit(c,"local-connector","ai."+job["kind"]+".complete",job["task_id"],{"plan_id":plan_id,"final_job":final_job})
        return {"id":job_id,"status":"complete"}

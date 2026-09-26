"""Task transitions and four-layer review. No task can self-accept."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import store


def _probe_video(path: Path) -> dict:
    ffprobe=shutil.which("ffprobe")
    if not ffprobe:
        return {"verified":False,"reason":"本机没有 ffprobe，视频技术属性未验证"}
    result=subprocess.run([ffprobe,"-v","error","-show_streams","-show_format","-of","json",str(path)],capture_output=True,text=True,timeout=25,check=False)
    if result.returncode:
        return {"verified":False,"reason":"视频无法由 ffprobe 解码元数据"}
    try: info=json.loads(result.stdout)
    except ValueError: return {"verified":False,"reason":"视频技术元数据不可解析"}
    streams=info.get("streams") or []
    video=next((s for s in streams if s.get("codec_type")=="video"),None)
    if not video:
        return {"verified":False,"reason":"文件无视频轨"}
    return {"verified":True,"duration":float((info.get("format") or {}).get("duration") or 0),"width":int(video.get("width") or 0),"height":int(video.get("height") or 0),"has_audio":any(s.get("codec_type")=="audio" for s in streams),"codec":video.get("codec_name")}


def technical_qc(task_id: str, actor: str="u_system") -> dict:
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        if task["status"]!="submitted": raise ValueError("只有已提交任务可做技术检查")
        delivery=store.latest_delivery(c,task_id)
        asset=store.record(c,"assets",delivery["asset_id"]) if delivery["asset_id"] else None
        input_assets=[dict(r) for r in c.execute("SELECT a.* FROM assets a JOIN json_each(?) j ON a.id=j.value",(task["input_assets"],))]
    findings=[]; evidence=[]; verified=True; failed=False
    for item in input_assets:
        if item["storage_ref"].startswith(("https://","studio://")):
            findings.append("远程锁定素材只核对登记版本；实际内容需由专业人员在原工具复核："+item["name"])
            verified=False
            continue
        path=Path(item["storage_ref"])
        if not path.is_file() or store.digest_file(path)!=item["sha256"]:
            findings.append("锁定输入资产已缺失或内容改变："+item["name"])
            failed=True
        else:
            evidence.append(item["id"]+":"+item["sha256"])
    if asset:
        path=Path(asset["storage_ref"])
        if not path.is_file():
            findings.append("提交文件不存在")
            failed=True
        elif store.digest_file(path)!=asset["sha256"]:
            findings.append("提交文件校验和与登记不符")
            failed=True
        elif path.stat().st_size==0:
            findings.append("提交文件为空")
            failed=True
        else:
            evidence.append(asset["id"]+":"+asset["sha256"])
            if asset["type"]=="video":
                probe=_probe_video(path)
                if not probe["verified"]:
                    findings.append(probe["reason"])
                    verified=False
                else:
                    qc_contract=store.parse(task["qc_contract"],{})
                    if probe["duration"]<=0 or probe["width"]<=0 or probe["height"]<=0:
                        findings.append("视频时长或尺寸无效")
                        failed=True
                    if qc_contract.get("min_width") and probe["width"]<qc_contract["min_width"]:
                        findings.append("视频宽度低于任务要求")
                        failed=True
                    if qc_contract.get("min_height") and probe["height"]<qc_contract["min_height"]:
                        findings.append("视频高度低于任务要求")
                        failed=True
                    if qc_contract.get("min_duration") and probe["duration"]<qc_contract["min_duration"]:
                        findings.append("视频时长低于任务要求")
                        failed=True
                    if qc_contract.get("max_duration") and probe["duration"]>qc_contract["max_duration"]:
                        findings.append("视频时长超过任务要求")
                        failed=True
                    evidence.append("ffprobe:"+store.dumps(probe))
            else:
                evidence.append("bytes:"+str(path.stat().st_size))
    else:
        verified=False
        findings.append("仅收到链接或平台任务编号；本机尚未取得并核验文件")
    result="fail" if failed else ("pass" if verified else "unverified")
    store.add_qc(task_id,delivery["id"],"technical",result,[],findings,actor,"sha256+ffprobe" if asset else "metadata_only","submitted_asset_only",evidence,actor)
    with store.connect() as c:
        if failed:
            store.update_task_status(c,task_id,{"submitted"},"revision_required",actor,"技术检查不通过："+"；".join(findings)[:350])
        else:
            store.update_task_status(c,task_id,{"submitted"},"technical_checked",actor)
    if failed:
        # This is a concrete fix request tied to the current delivery version.
        with store.connect() as c:
            task=store.record(c,"tasks",task_id)
            fid="f_"+store.secrets.token_hex(8)
            c.execute("INSERT INTO feedback(id,task_id,deliverable_id,version,kind,problem,change_request,preserve,author_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(fid,task_id,delivery["id"],delivery["version"],"task","；".join(findings),"修复上述文件或输入锁定问题后重新提交","保留未受影响的有效素材","u_system",store.now()))
            refs=store.parse(task["feedback_refs"],[])+[fid]
            c.execute("UPDATE tasks SET feedback_refs=? WHERE id=?",(store.dumps(refs),task_id))
    return {"stage":"technical","result":result,"findings":findings,"evidence_refs":evidence}


def ai_skip(task_id: str, user: dict, reason: str):
    if not reason.strip(): raise ValueError("必须说明 AI 初检为何无法完成")
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        if task["status"]!="technical_checked": raise ValueError("当前状态不能跳过 AI 初检")
        delivery=store.latest_delivery(c,task_id)
    store.add_qc(task_id,delivery["id"],"ai","unverified",[],["AI 初检未完成："+reason[:600]],user["id"],"manual_skip","no_ai_review",[],user["id"])
    with store.connect() as c:
        store.update_task_status(c,task_id,{"technical_checked"},"ai_prechecked",user["id"])


def human_review(task_id: str, user: dict, verdict: str, findings: str, change_request: str="", preserve: str="", method: str="", coverage: str="", evidence_refs=None):
    if verdict not in {"pass","fail"} or not method.strip() or not coverage.strip():
        raise ValueError("人工复核必须写明结论、方法和覆盖范围")
    if verdict=="fail" and (not findings.strip() or not change_request.strip()):
        raise ValueError("不通过时必须说明问题与具体修改办法")
    if verdict=="pass" and not findings.strip():
        raise ValueError("通过时请写明实际核验要点")
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        if task["status"]!="ai_prechecked": raise ValueError("须先完成技术检查和 AI 初检")
        delivery=store.latest_delivery(c,task_id)
    context=store.parse(task["context"],{})
    if verdict=="pass" and context.get("lesson_stage") and context.get("required_for_publish"):
        import lesson_pipeline
        lesson_pipeline.require_stage_ready(context["lesson_move_id"],context["lesson_stage"])
    store.add_qc(task_id,delivery["id"],"human",verdict,store.parse(task["qc_contract"],{}).get("criteria",[]),[findings],user["id"],method,coverage,evidence_refs or [],user["id"])
    if verdict=="fail":
        store.feedback(task_id,delivery["id"],user,"task",findings,change_request,preserve)
    else:
        with store.connect() as c:
            if task["founder_required"]:
                store.update_task_status(c,task_id,{"ai_prechecked"},"human_review",user["id"])
            else:
                store.update_task_status(c,task_id,{"ai_prechecked"},"accepted",user["id"])
        if not task["founder_required"] and context.get("lesson_stage"):
            import lesson_pipeline
            lesson_pipeline.record_task_acceptance(task_id)
    return {"status":"revision_required" if verdict=="fail" else ("human_review" if task["founder_required"] else "accepted")}


def founder_review(task_id: str,user: dict,verdict: str,findings: str,change_request: str="",preserve: str="",method: str="",coverage: str="",evidence_refs=None):
    if user["role"]!="founder": raise PermissionError("仅创始人可做关键艺术验收")
    if verdict not in {"pass","fail"} or not findings.strip() or not method.strip() or not coverage.strip():
        raise ValueError("创始人验收需填写结论、观察、方法和范围")
    if verdict=="fail" and not change_request.strip(): raise ValueError("返修须给出具体改法")
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        if task["status"]!="human_review" or not task["founder_required"]:
            raise ValueError("该任务尚未进入创始人关键验收")
        delivery=store.latest_delivery(c,task_id)
    store.add_qc(task_id,delivery["id"],"founder",verdict,[],[findings],user["id"],method,coverage,evidence_refs or [],user["id"])
    if verdict=="fail":
        store.feedback(task_id,delivery["id"],user,"task",findings,change_request,preserve)
    else:
        with store.connect() as c:
            store.update_task_status(c,task_id,{"human_review"},"accepted",user["id"])
        if store.parse(task["context"],{}).get("lesson_stage"):
            import lesson_pipeline
            lesson_pipeline.record_task_acceptance(task_id)
    return {"status":"revision_required" if verdict=="fail" else "accepted"}


def record_pilot(task_id: str,user: dict,data: dict):
    required={"first_understood","knew_next_step","materials_clear","ai_prepared_clear","submitted_success","revision_clear","duration_minutes","founder_interventions","extra_explanations","blockers"}
    if not required.issubset(data): raise ValueError("真人测试记录字段不完整")
    with store.connect() as c:
        task=store.record(c,"tasks",task_id)
        employee_id=task["assignee_id"]
        if not employee_id or (user["role"]=="employee" and user["id"]!=employee_id):
            raise PermissionError("只能记录该任务的实际员工体验")
        if data["duration_minutes"] is not None and (float(data["duration_minutes"])<0 or float(data["duration_minutes"])>10000): raise ValueError("耗时无效")
        for key in ("founder_interventions","extra_explanations"):
            if int(data[key])<0 or int(data[key])>100: raise ValueError("次数无效")
        oid="po_"+store.secrets.token_hex(8)
        c.execute("INSERT INTO pilot_observations(id,task_id,employee_id,first_understood,knew_next_step,materials_clear,ai_prepared_clear,submitted_success,revision_clear,duration_minutes,founder_interventions,extra_explanations,blockers,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(oid,task_id,employee_id,*[None if data[k] is None else int(bool(data[k])) for k in ("first_understood","knew_next_step","materials_clear","ai_prepared_clear","submitted_success","revision_clear")],data["duration_minutes"],int(data["founder_interventions"]),int(data["extra_explanations"]),str(data["blockers"])[:3000],user["id"],store.now()))
        store.audit(c,user["id"],"pilot.observe",task_id,{"observation_id":oid})
        return oid

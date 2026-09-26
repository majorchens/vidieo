"""Mac-side AI Studio executor; provider keys remain on this machine.

Text uses the proven Wanjie DeepSeek Chat client. Video uses the same Company
Workflow media ledger, Runy routing, idempotency and result recovery as Martial.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from local_connector import request
from ai_studio import IMAGE_MODEL, IMAGE_PRICE, IMAGE_WIDTH, IMAGE_HEIGHT, MAX_IMAGE
from martial_connector import (WORKFLOW, MAX_VIDEO_FILE, _NoRedirect, _file_sha256,
                               _saved_workflow_media, _settled_workflow_media,
                               _submit_workflow_media, _technical, workflow_request)


def _safe_error(value) -> str:
    from common import redact
    return redact(str(value))[:300]


def _text(spec: dict) -> dict:
    import provider
    if spec.get("provider") != "wanjie" or spec.get("model") != "deepseek-v4.1-flash":
        raise ValueError("未获批准的文本路由")
    inputs=spec["inputs"]
    context=inputs.get("project_context") or {}
    file_text=inputs.get("file_text") or ""
    assets=inputs.get("asset_texts") or []
    if not isinstance(assets,list) or len(assets)>5:raise ValueError("参考素材数量无效")
    role=("你是 Yoodun Work OS 公司 AI 创作工具。只依据输入内容作答，不假称读取了未给出的资料。"
          "必须返回一个 JSON 对象，包含 title 和 text 字段；可以附加 ideas、outline 等结构化字段。"
          "不得输出凭据，不得把建议说成已经执行或已发布。")
    capability=spec["capability"]
    if capability=="ideas":
        role+=("对找创意请求优先给 3 个可执行方案，每个含 core_idea、hook、difficulty、"
               "required_assets、platform；在 text 中给简短导语。")
    if capability=="analyze":role+="分析文件时说明所依据的文件名以及被截断的内容限制。"
    if capability=="assistant":role+="你是员工的日常助手，只讨论建议，不更改项目事实、任务状态或预算。"
    brief={"capability":capability,"scenario":inputs.get("scenario"),"prompt":inputs.get("prompt"),
           "requirements":inputs.get("requirements"),"language":inputs.get("language"),
           "context":context,"file_name":inputs.get("file_name"),"file_text":file_text,
           "selected_assets":assets}
    reply=provider.chat([{"role":"system","content":role},{"role":"user","content":json.dumps(brief,ensure_ascii=False)}],
                        model="deepseek-v4.1-flash",max_tokens=1800,thinking="disabled",final=True)
    if reply.get("model") and reply["model"]!="deepseek-v4.1-flash":
        raise RuntimeError("万界返回非指定 DeepSeek 模型")
    choices=reply.get("choices") or []
    message=choices[0].get("message",{}) if choices else {}
    content=message.get("content")
    if not isinstance(content,str) or not content.strip():raise RuntimeError("DeepSeek 未返回文本")
    raw=content.strip()
    if raw.startswith("```json") and raw.endswith("```"):raw=raw[7:-3].strip()
    try:output=json.loads(raw)
    except ValueError:output={"title":"AI 创作结果","text":content.strip()}
    if not isinstance(output,dict):output={"title":"AI 创作结果","text":content.strip()}
    if not isinstance(output.get("text"),str):output["text"]=json.dumps(output,ensure_ascii=False)
    if len(json.dumps(output,ensure_ascii=False))>120_000:raise ValueError("AI 结果过长；本次不重复付费调用")
    return {"status":"completed","output":output,"usage":reply.get("usage") or {},
            "model":reply.get("model") or "deepseek-v4.1-flash",
            "provider_job_id":str(reply.get("id") or "")[:150]}


def _save_report(path: Path,report: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+"."+str(os.getpid())+".tmp")
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as stream:
        json.dump(report,stream,ensure_ascii=False)
        stream.flush();os.fsync(stream.fileno())
    tmp.replace(path)


def _report(url: str,token: str,jid: str,body: dict):
    if body.get("video_base64") or body.get("image_base64"):
        payload=json.dumps(body,ensure_ascii=False).encode()
        req=urllib.request.Request(url+"/api/ai-studio/connector/jobs/"+jid+"/report",payload,
                                   headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method="POST")
        with urllib.request.urlopen(req,timeout=180) as response:
            return json.load(response)
    return request(url+"/api/ai-studio/connector/jobs/"+jid+"/report",token,"POST",body)


def _return_saved_video(url: str, token: str, jid: str, saved: dict) -> dict:
    """Retry file transfer and report for the same paid result, never regenerate."""
    if "report" not in saved:return _report(url,token,jid,saved)
    report=saved["report"]
    path=Path(saved["local_video_path"]).resolve(strict=True)
    if not path.is_relative_to((WORKFLOW/"tasks").resolve()) or path.suffix.lower()!=".mp4":
        raise ValueError("工作流视频不在任务输出目录")
    sha=report.get("video_sha256")
    if not isinstance(sha,str) or _file_sha256(path)!=sha:
        raise ValueError("本地生成文件校验值变化，暂停回传")
    size=path.stat().st_size
    if not 0<size<=MAX_VIDEO_FILE:raise ValueError("视频为空或超过 512MB")
    with path.open("rb") as stream:
        req=urllib.request.Request(url+"/api/ai-studio/connector/jobs/"+jid+"/video-file",stream,
            headers={"Authorization":"Bearer "+token,"Content-Type":"video/mp4",
                     "Content-Length":str(size),"X-File-SHA256":sha},method="POST")
        with urllib.request.build_opener(_NoRedirect).open(req,timeout=600) as response:
            json.load(response)
    return _report(url,token,jid,report)


def _text_job(url: str,token: str,item: dict,scratch: Path) -> None:
    jid=item["id"]
    result=scratch/(jid+".text-result.json")
    if result.exists():
        _report(url,token,jid,json.loads(result.read_text()))
        result.unlink();return
    if item["status"]=="dispatching":
        _report(url,token,jid,{"status":"unknown_submission","error":"本机 DeepSeek 调用中断；未自动重发"})
        return
    if item["status"]!="queued":return
    spec=request(url+"/api/ai-studio/connector/jobs/"+jid+"/claim",token,"POST",{})
    if spec.get("request_key")!=item["request_key"]:raise ValueError("任务幂等编号不一致")
    try:report=_text(spec)
    except Exception as exc:
        report={"status":"unknown_submission" if type(exc).__name__=="UnknownSubmission" else "failed",
                "error":type(exc).__name__+": "+_safe_error(exc)}
    _save_report(result,report)
    _report(url,token,jid,report)
    result.unlink()


def _video_job(url: str,token: str,item: dict,scratch: Path) -> None:
    jid=item["id"]
    result=scratch/(jid+".video-result.json")
    if result.exists():
        _return_saved_video(url,token,jid,json.loads(result.read_text()))
        result.unlink();return
    spec=request(url+"/api/ai-studio/connector/jobs/"+jid+"/claim",token,"POST",{})
    if spec.get("request_key")!=item["request_key"]:raise ValueError("任务幂等编号不一致")
    from video_routing import resolve
    alias=spec.get("model_alias")
    approved={"sd2.0":"doubao-seedance-2.0","sd2.5":"doubao-seedance-2-5"}
    if spec.get("provider")!="runy" or approved.get(alias)!=spec.get("model"):
        _report(url,token,jid,{"status":"failed","error":"视频路由未获批准"});return
    if {k:spec["inputs"].get(k) for k in ("duration","resolution","ratio","count")}!={
            "duration":5,"resolution":"480p","ratio":"16:9","count":1}:
        _report(url,token,jid,{"status":"failed","error":"此规格没有已验证报价"});return
    route=resolve(alias,provider_name="runy")
    if route!={"provider":"runy","model":spec["model"]}:
        _report(url,token,jid,{"status":"failed","error":"固定模型路由与本机配置不一致"});return
    project_id=spec.get("project_id")
    if project_id not in {"wuxiang","diaojianghu"}:
        _report(url,token,jid,{"status":"failed","error":"当前项目不在已验证视频执行账本范围"});return
    ledger=spec.get("local_job_id")
    local_media=spec.get("local_media_id")
    upstream=spec.get("provider_job_id") or ""
    submitted_attempt=False
    try:
        if not ledger:
            job=workflow_request("POST","/api/jobs",{
                "brief":"Work OS AI 创作视频账本 "+jid,
                "project":"万象武境编剧" if project_id=="wuxiang" else "钓江湖",
                "kind":"general","request_key":"workos:ai-studio:ledger:"+jid,
                "mode":"ledger","max_steps":1,"max_output_tokens":128,
                "capability_budget_cny":0,"capability_max_calls":0})
            ledger=job["id"]
        if local_media:
            row=workflow_request("GET","/api/media?"+urlencode({"id":local_media}))
        elif spec["status"]!="queued":
            # A prior create may have lost the reply. Never issue a second POST.
            saved=_settled_workflow_media(spec["request_key"])
            if not saved or (saved["status"]=="submitting" and not saved.get("upstream_id")):
                _report(url,token,jid,{"status":"unknown_submission",
                    "error":"原提交无可核实的任务结果；不自动重提",
                    "local_job_id":ledger,"local_media_id":(saved or {}).get("id")})
                return
            local_media=saved["id"]
            row=workflow_request("GET","/api/media?"+urlencode({"id":local_media}))
        else:
            submitted_attempt=True
            row=_submit_workflow_media({
                "job_id":ledger,"kind":"video","prompt":spec["inputs"]["prompt"],
                "request_key":spec["request_key"],"quoted_cost_cny":spec["reserved_cost"],
                "budget_cny":spec["reserved_cost"],"quote_source":spec["quote_source"],
                "model":alias,"provider_name":"runy","duration":5,
                "resolution":"480p","ratio":"16:9","generate_audio":False,
                "image_urls":[],"video_urls":[]})
        local_media=row["id"]
        status=row["status"]
        upstream=str(row.get("upstream_id") or upstream)
        if status=="submitting" and not upstream:
            _report(url,token,jid,{"status":"unknown_submission",
                "error":"原润元提交仍无任务编号；不自动重发",
                "local_job_id":ledger,"local_media_id":local_media});return
        if status=="uncertain" and upstream:
            try:
                row=workflow_request("GET","/api/media?"+urlencode({"id":local_media,"refresh":"true"}))
                status=row["status"]
            except Exception:pass
        if status=="uncertain":
            _report(url,token,jid,{"status":"unknown_submission",
                "error":"供应商提交待核实；仅查询原任务或账单，不自动重发",
                "local_job_id":ledger,"local_media_id":local_media,"provider_job_id":upstream});return
        if status in {"rejected","failed","expired","cancelled"}:
            _report(url,token,jid,{"status":"failed","error":_safe_error((row.get("response") or {}).get("error") or status),
                "local_job_id":ledger,"local_media_id":local_media,"provider_job_id":upstream});return
        if status not in {"succeeded","success","done","completed"}:
            _report(url,token,jid,{"status":"running" if status in {"running","queued"} else "submitted",
                "local_job_id":ledger,"local_media_id":local_media,"provider_job_id":upstream});return
        files=(row.get("response") or {}).get("local_files") or []
        if not files:
            row=workflow_request("GET","/api/media?"+urlencode({"id":local_media,"refresh":"true"}))
            files=(row.get("response") or {}).get("local_files") or []
            if not files:
                _report(url,token,jid,{"status":"download_pending","local_job_id":ledger,
                    "local_media_id":local_media,"provider_job_id":upstream});return
        path=Path(files[0]).resolve(strict=True)
        if not path.is_relative_to((WORKFLOW/"tasks").resolve()) or path.suffix.lower()!=".mp4":
            raise ValueError("工作流视频不在任务输出目录")
        if not 0<path.stat().st_size<=MAX_VIDEO_FILE:raise ValueError("视频为空或超过 512MB")
        report={"status":"completed","video_sha256":_file_sha256(path),
                "technical":_technical(path),"local_job_id":ledger,"local_media_id":local_media,
                "provider_job_id":upstream,"actual_cost":(row.get("billing") or {}).get("actual_cost_cny")}
        _save_report(result,{"report":report,"local_video_path":str(path)})
        _return_saved_video(url,token,jid,{"report":report,"local_video_path":str(path)})
        result.unlink()
    except Exception as exc:
        if result.exists():return
        if submitted_attempt:
            try:saved=_settled_workflow_media(spec["request_key"])
            except Exception:saved=None
            if saved and saved.get("upstream_id"):
                _report(url,token,jid,{"status":"submitted","local_job_id":ledger,
                    "local_media_id":saved["id"],"provider_job_id":saved["upstream_id"]});return
            _report(url,token,jid,{"status":"unknown_submission",
                "error":type(exc).__name__+": "+_safe_error(exc)[:250],"local_job_id":ledger,
                "local_media_id":(saved or {}).get("id") or local_media,"provider_job_id":upstream});return
        if spec["status"]=="queued":
            _report(url,token,jid,{"status":"failed","error":type(exc).__name__+": "+_safe_error(exc)[:250]});return
        # Read-only polling failed; retain the original job for the next cycle.


def _image_job(url: str,token: str,item: dict,scratch: Path) -> None:
    """Submit once through the existing Company Workflow image ledger and recover by ID."""
    jid=item["id"]
    result=scratch/(jid+".image-result.json")
    if result.exists():
        _report(url,token,jid,json.loads(result.read_text()))
        result.unlink();return
    spec=request(url+"/api/ai-studio/connector/jobs/"+jid+"/claim",token,"POST",{})
    if spec.get("request_key")!=item["request_key"]:raise ValueError("任务幂等编号不一致")
    settings=spec.get("inputs") or {}
    if (spec.get("provider")!="wanjie" or spec.get("model")!=IMAGE_MODEL or {
            k:settings.get(k) for k in ("width","height","count")}!={
            "width":IMAGE_WIDTH,"height":IMAGE_HEIGHT,"count":1} or spec.get("reserved_cost")!=IMAGE_PRICE or
            not isinstance(spec.get("quote_source"),str) or not spec["quote_source"]):
        _report(url,token,jid,{"status":"failed","error":"图片路由或报价与已验证规格不一致"});return
    project=spec.get("workflow_project")
    if not isinstance(project,str) or not project:
        _report(url,token,jid,{"status":"failed","error":"项目尚无公司工作流映射"});return
    prompt=settings.get("prompt") or ""
    requirements=settings.get("requirements") or ""
    if requirements:prompt+="\n补充要求："+requirements
    if not prompt.strip() or len(prompt)>17000:
        _report(url,token,jid,{"status":"failed","error":"图片描述为空或过长"});return
    ledger=spec.get("local_job_id")
    local_media=spec.get("local_media_id")
    upstream=spec.get("provider_job_id") or ""
    submitted_attempt=False
    try:
        if not ledger:
            job=workflow_request("POST","/api/jobs",{
                "brief":"Work OS AI 创作图片账本 "+jid,
                "project":project,"kind":"general",
                "request_key":"workos:ai-studio:image:ledger:"+jid,
                "mode":"ledger","max_steps":1,"max_output_tokens":128,
                "capability_budget_cny":0,"capability_max_calls":0})
            ledger=job["id"]
        if local_media:
            row=workflow_request("GET","/api/media?"+urlencode({"id":local_media}))
        elif spec["status"]!="queued":
            saved=_settled_workflow_media(spec["request_key"])
            if not saved or (saved["status"]=="submitting" and not saved.get("upstream_id")):
                _report(url,token,jid,{"status":"unknown_submission",
                    "error":"原图片提交无可核实的任务结果；不自动重提",
                    "local_job_id":ledger,"local_media_id":(saved or {}).get("id")})
                return
            local_media=saved["id"]
            row=workflow_request("GET","/api/media?"+urlencode({"id":local_media}))
        else:
            submitted_attempt=True
            row=_submit_workflow_media({
                "job_id":ledger,"kind":"image","prompt":prompt,
                "request_key":spec["request_key"],"quoted_cost_cny":IMAGE_PRICE,
                "budget_cny":IMAGE_PRICE,"quote_source":spec["quote_source"],
                "model":IMAGE_MODEL,"provider_name":"wanjie",
                "width":IMAGE_WIDTH,"height":IMAGE_HEIGHT})
        local_media=row["id"]
        status=row["status"]
        upstream=str(row.get("upstream_id") or upstream)
        if status=="submitting" and not upstream:
            _report(url,token,jid,{"status":"unknown_submission",
                "error":"原万界图片提交仍无任务编号；不自动重发",
                "local_job_id":ledger,"local_media_id":local_media});return
        if status=="uncertain" and upstream:
            try:
                row=workflow_request("GET","/api/media?"+urlencode({"id":local_media,"refresh":"true"}))
                status=row["status"]
            except Exception:pass
        if status=="uncertain":
            _report(url,token,jid,{"status":"unknown_submission",
                "error":"供应商提交待核实；仅查询原任务或账单，不自动重发",
                "local_job_id":ledger,"local_media_id":local_media,"provider_job_id":upstream});return
        if status in {"rejected","failed","expired","cancelled"}:
            _report(url,token,jid,{"status":"failed","error":_safe_error((row.get("response") or {}).get("error") or status),
                "local_job_id":ledger,"local_media_id":local_media,"provider_job_id":upstream});return
        if status not in {"succeeded","success","done","completed"}:
            _report(url,token,jid,{"status":"running" if status in {"running","queued"} else "submitted",
                "local_job_id":ledger,"local_media_id":local_media,"provider_job_id":upstream});return
        files=(row.get("response") or {}).get("local_files") or []
        if not files:
            row=workflow_request("GET","/api/media?"+urlencode({"id":local_media,"refresh":"true"}))
            files=(row.get("response") or {}).get("local_files") or []
            if not files:
                _report(url,token,jid,{"status":"download_pending","local_job_id":ledger,
                    "local_media_id":local_media,"provider_job_id":upstream});return
        path=Path(files[0]).resolve(strict=True)
        if not path.is_relative_to((WORKFLOW/"tasks").resolve()) or path.suffix.lower() not in {".png",".jpg",".webp"}:
            raise ValueError("工作流图片不在任务输出目录")
        if path.stat().st_size>MAX_IMAGE:raise ValueError("图片超过 12MB")
        content=path.read_bytes()
        if not (content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8\xff") or
                (content.startswith(b"RIFF") and content[8:12]==b"WEBP")):
            raise ValueError("图片缺少有效格式标识")
        report={"status":"completed","image_base64":base64.b64encode(content).decode(),
                "model":IMAGE_MODEL,"local_job_id":ledger,"local_media_id":local_media,
                "provider_job_id":upstream,"actual_cost":(row.get("billing") or {}).get("actual_cost_cny"),
                "billing_source":(row.get("billing") or {}).get("source")}
        _save_report(result,report)
        _report(url,token,jid,report)
        result.unlink()
    except Exception as exc:
        if result.exists():return
        if submitted_attempt:
            try:saved=_settled_workflow_media(spec["request_key"])
            except Exception:saved=None
            if saved and saved.get("upstream_id"):
                _report(url,token,jid,{"status":"submitted","local_job_id":ledger,
                    "local_media_id":saved["id"],"provider_job_id":saved["upstream_id"]});return
            _report(url,token,jid,{"status":"unknown_submission",
                "error":type(exc).__name__+": "+_safe_error(exc)[:250],"local_job_id":ledger,
                "local_media_id":(saved or {}).get("id") or local_media,"provider_job_id":upstream});return
        if spec["status"]=="queued":
            _report(url,token,jid,{"status":"failed","error":type(exc).__name__+": "+_safe_error(exc)[:250]});return
        # Read-only recovery failed; keep the original media ID for the next cycle.


def run_once(config: dict) -> None:
    url=config["url"].rstrip("/")
    token=config["token"]
    scratch=Path(config["scratch"])/"ai_studio"
    scratch.mkdir(parents=True,exist_ok=True)
    jobs=request(url+"/api/ai-studio/connector/jobs",token)["jobs"]
    for item in jobs:
        try:
            if item["capability"]=="video":_video_job(url,token,item,scratch)
            elif item["capability"]=="image":_image_job(url,token,item,scratch)
            else:_text_job(url,token,item,scratch)
        except Exception as exc:
            print("AI 创作任务处理暂不可用："+type(exc).__name__+" "+_safe_error(exc)[:150],flush=True)


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--config",required=True);parser.add_argument("--once",action="store_true")
    args=parser.parse_args();cfg=json.loads(Path(args.config).read_text())
    if not cfg["url"].startswith("https://"):raise SystemExit("连接器只允许 HTTPS Work OS")
    while True:
        run_once(cfg)
        if args.once:break
        time.sleep(8)

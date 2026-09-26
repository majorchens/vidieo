"""Mac-side martial jobs. Reuse Company Workflow media routing and storage.

This process never uploads a provider key or the connector token as a file.
Company Workflow owns the upstream video submission, polling and download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from local_connector import request

WORKFLOW=Path("/Users/majorchen/Documents/Codex/2026-09-18/new-chat/outputs/company-workflow")
MASTER_SOURCES=Path(__file__).resolve().parents[1]/"registry"/"martial_master_sources.json"
MAX_VIDEO_FILE=512_000_000
MAX_REFERENCE_CLIP_FILE=200_000_000
sys.path.insert(0,str(WORKFLOW/"app"))


def workflow_request(method,path,payload=None):
    import client
    return client.request(method,path,payload)


def _submit_workflow_media(payload: dict) -> dict:
    """Allow the synchronous Runy create call to finish before treating it as lost."""
    import httpx
    from common import config
    from service import local_token
    endpoint="http://127.0.0.1:"+str(config()["port"])+"/api/media"
    with httpx.Client(trust_env=False,timeout=httpx.Timeout(180,connect=10)) as client:
        response=client.post(endpoint,headers={"X-Workflow-Token":local_token()},json=payload)
        response.raise_for_status()
        return response.json()


def _saved_workflow_media(request_key: str) -> dict | None:
    """Read the local ledger after a lost HTTP reply; never submit it again."""
    import common
    with common.db() as c:
        row=c.execute("SELECT id,status,upstream_id FROM media WHERE request_key=?",(request_key,)).fetchone()
    return dict(row) if row else None


def _settled_workflow_media(request_key: str, wait_seconds: int = 170) -> dict | None:
    """Wait for the original request key; a pending create must never be retried."""
    deadline=time.monotonic()+wait_seconds
    while True:
        saved=_saved_workflow_media(request_key)
        if not saved or saved["status"]!="submitting" or saved.get("upstream_id"):
            return saved
        if time.monotonic()>=deadline:return saved
        time.sleep(min(2,max(0,deadline-time.monotonic())))


def _package(facts: dict) -> dict:
    import provider
    master=facts.get("master") or {}
    manifest=json.loads(MASTER_SOURCES.read_text(encoding="utf-8"))
    pinned=manifest.get(str(master.get("id") or ""))
    if not pinned or master.get("canonical_sha256")!=pinned["sha256"]:
        raise ValueError("老师 V2 原文与生产包锁定版本不一致")
    role_path=Path(pinned["path"])
    if not role_path.is_file() or role_path.stat().st_size>20_000:
        raise ValueError("老师 V2 原文不存在或超出单次输入上限")
    role_bytes=role_path.read_bytes()
    if hashlib.sha256(role_bytes).hexdigest()!=pinned["sha256"]:
        raise ValueError("老师 V2 原文校验失败")
    role_text=role_bytes.decode("utf-8")
    if re.search(r"(?:api[_-]?key|password|token|secret)\s*[:=]\s*\S+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",role_text,re.I):
        raise ValueError("老师 V2 原文疑似包含凭据")
    system=(
        "你是万象武境武术数字资产的生产包撰写助手。只返回合法 JSON 对象，不要 Markdown。"
        "严格区分 facts 与 ai_suggestions。武学动作、中英文表达、旁白、NPC版本及真人动作时刻都只能引用输入事实，"
        "不得擅自纠正、增补或把未观看的视频说成已验证。已锁定真人参考视频及经员工确认的区间、姿态和关键时刻是可用的动作输入；"
        "你未直接观看视频画面属于能力边界，必须在建议中说明，但不要仅因此填写 missing_inputs。"
        "production_brief 是员工确认的制作意图，不是你对视频的视觉分析。若员工要求按原片的示范、跟练等阶段复刻，"
        "严格保留员工明确给出的阶段顺序、总时长与时间标记；未提供的分段秒点、镜头、动作细节不得猜测。"
        "未标注的示范/跟练切换秒点、逐帧动作标记应放入 ai_suggestions 作为后续看片标注事项，"
        "不能写入 missing_inputs 阻断已经锁定完整视频的 AI 准备。"
        "video_plans 中 teaching 是讲解演示片，practice 是跟教练跟练片；它们是两个独立视频，"
        "各自引用其 source_start/source_end 和 target_duration。目标时长可超过 30 秒，"
        "不得自行缩成 5 秒，也不得把两片混成一个视频。未提供的分段秒点不得猜测。"
        "teaching_prompt 和 practice_prompt 都要明确以对应真人区间为动作参考，不得把自主创作的镜头建议伪装成原片事实，"
        "也不得宣称仅凭文字提示词能保证逐帧一比一复制。角色定版与武学事实仍须保持一致。"
        "真正缺少生成必需的角色图或动作事实时才列入 missing_inputs。"
        "教学视频和演练视频分别给出用途；标准视频不烧入未来动态 TTS 反馈。"
    )
    user=("已确认的文字和锁定元数据："+json.dumps(facts,ensure_ascii=False)[:14000]+"\n"
          "对应老师的 V2 人物原文（仅用于角色一致性，不能覆盖武学动作事实）：\n"+role_text+"\n"
          "输出字段：move_summary（简短文字），motion_breakdown（文字步骤数组），character_constraints（数组），"
          "shot_camera_plan（数组），teaching_prompt（讲解演示片提示词），practice_prompt（跟教练跟练片提示词），"
          "seedance_prompt（兼容旧展示，填 teaching_prompt），negative_constraints（数组），"
          "reference_mapping（数组），qc_checklist（数组），missing_inputs（数组），facts（空对象，占位；原始事实由系统保存），"
          "ai_suggestions（只列镜头/生成建议）。数组各不超过 5 项，每项不超过 80 字；"
          "move_summary 不超过 150 字，每条视频提示词不超过 1000 字，总输出尽量控制在 5000 字内。"
          "不要生成未提供的招式事实。若员工未标注示范与跟练的切换秒点，在 ai_suggestions 写明需人工标注，"
          "不要杜撰秒点，也不要仅因该秒点或逐帧标注未提供就填写 missing_inputs。")
    reply=provider.chat([{"role":"system","content":system},{"role":"user","content":user}],
                        model="deepseek-v4.1-flash",max_tokens=6000,thinking="disabled",final=True)
    if reply.get("model") and reply["model"]!="deepseek-v4.1-flash":raise RuntimeError("万界返回非指定模型")
    choices=reply.get("choices") or []
    finish_reason=choices[0].get("finish_reason") if choices else None
    if finish_reason and finish_reason!="stop":
        raise ValueError("DeepSeek 输出未正常结束（finish_reason="+str(finish_reason)[:40]+"），JSON 可能不完整")
    content=choices[0].get("message",{}).get("content") if choices else None
    if not isinstance(content,str):raise RuntimeError("DeepSeek 未返回文本")
    value=content.strip()
    if value.startswith("```json") and value.endswith("```"):value=value[7:-3].strip()
    body=json.loads(value)
    if not isinstance(body,dict):raise ValueError("生产包不是 JSON 对象")
    needed={"move_summary","motion_breakdown","character_constraints","shot_camera_plan","teaching_prompt","practice_prompt","seedance_prompt",
            "negative_constraints","reference_mapping","qc_checklist","missing_inputs","facts","ai_suggestions"}
    if not needed.issubset(body):raise ValueError("生产包字段不完整")
    if any(not isinstance(body[key],str) or not body[key].strip() for key in ("teaching_prompt","practice_prompt")):
        raise ValueError("两个独立视频的生成提示词均须填写")
    return {"status":"complete","body":body,"usage":reply.get("usage") or {},
            "provider_job_id":str(reply.get("id") or "")}


def _technical(path: Path) -> dict:
    with path.open("rb") as f:header=f.read(32)
    if b"ftyp" not in header:return {"result":"fail","notes":"文件缺少 MP4 标识"}
    try:
        import av
        with av.open(str(path)) as container:
            stream=next(iter(container.streams.video),None)
            if stream is None:return {"result":"fail","notes":"没有可识别的视频流"}
            first=next(container.decode(video=0),None)
            if first is None:return {"result":"fail","notes":"视频流没有可解码画面"}
            seconds=float(container.duration/av.time_base) if container.duration is not None else None
            return {"result":"pass","notes":"MP4 容器和首帧可解码；动作及角色一致性仍须真人 QC",
                    "duration":seconds,"size":path.stat().st_size,"video":{"codec_name":stream.codec_context.name,
                    "width":stream.width,"height":stream.height,"r_frame_rate":str(stream.average_rate)}}
    except ImportError:
        pass
    except Exception as exc:
        return {"result":"fail","notes":"视频不能解码："+type(exc).__name__}
    command=["ffprobe","-v","error","-show_entries","format=duration,size:stream=codec_name,width,height,r_frame_rate",
             "-of","json",str(path)]
    try:
        p=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30,check=False)
    except (OSError,subprocess.TimeoutExpired):
        try:
            import martial
            info=martial._probe_video_file(path)
            ffmpeg=_ffmpeg_binary({})
            first=subprocess.run([ffmpeg,"-nostdin","-hide_banner","-loglevel","error", "-i",str(path),
                                  "-frames:v","1","-f","image2pipe","-vcodec","png","-"],capture_output=True,
                                 timeout=30,check=False)
            if first.returncode or not first.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                return {"result":"fail","notes":"FFmpeg 不能解码首帧："+first.stderr.decode(errors="replace")[-220:]}
            return {"result":"pass","notes":"MP4 元数据与首帧可解码；动作及角色一致性仍须真人 QC",
                    "duration":info["duration"],"size":path.stat().st_size,
                    "video":{"width":info["width"],"height":info["height"]}}
        except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired):
            return {"result":"unverified","notes":"MP4 标识通过；本机媒体探针未完成，需人工检查编码与播放"}
    if p.returncode:return {"result":"fail","notes":"ffprobe 不能识别媒体："+p.stderr[:220]}
    data=json.loads(p.stdout)
    streams=data.get("streams") or []
    videos=[s for s in streams if s.get("width") and s.get("height")]
    if not videos:return {"result":"fail","notes":"没有可识别的视频流"}
    fmt=data.get("format") or {}
    return {"result":"pass","notes":"容器和视频流可识别；动作及角色一致性仍须真人 QC",
            "duration":fmt.get("duration"),"size":fmt.get("size"),"video":videos[0]}


def _report(url,token,kind,jid,body):
    if kind!="media" or body.get("status")!="succeeded":
        return request(url+"/api/martial/connector/"+kind+"/"+jid+"/report",token,"POST",body)
    payload=json.dumps(body,ensure_ascii=False).encode()
    req=urllib.request.Request(url+"/api/martial/connector/media/"+jid+"/report",payload,
                               headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=180) as response:
        return json.load(response)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def _file_sha256(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def _signed_media_url(value: str, base_url: str, prefix: str) -> str:
    if not isinstance(value,str):raise ValueError("动作参考链接无效")
    requested=urlsplit(value);origin=urlsplit(base_url)
    if (requested.scheme,requested.netloc)!=(origin.scheme,origin.netloc) or requested.scheme!="https":
        raise ValueError("动作参考链接不属于当前工作台")
    expected=origin.path.rstrip("/")+prefix
    if ((not requested.path.startswith(expected) if prefix.endswith("/") else requested.path!=expected)
        or not requested.query):
        raise ValueError("必须使用工作台签发的动作参考链接")
    return value


def _ffmpeg_binary(config: dict) -> str:
    options=[config.get("ffmpeg"),shutil.which("ffmpeg"),
             "/Users/majorchen/Library/Application Support/bilibili/ffmpeg/ffmpeg"]
    for candidate in options:
        if candidate and Path(candidate).is_file() and os.access(candidate,os.X_OK):return str(candidate)
    raise RuntimeError("本机缺少 FFmpeg；不能裁剪或合成完整视频")


def _download_source(spec: dict, base_url: str, scratch: Path, jid: str) -> Path:
    """Download the locked original once; it is never passed to the provider."""
    import martial
    value=_signed_media_url(spec.get("reference_source_url"),base_url,"/api/martial/source/")
    if not re.fullmatch(r"mj_[a-f0-9]{16}",jid):raise ValueError("视频作业编号无效")
    expected_hash=spec.get("reference_sha256")
    if expected_hash is not None and not re.fullmatch(r"[a-f0-9]{64}",str(expected_hash)):
        raise ValueError("原片校验值无效")
    folder=scratch/"reference-clips"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=folder/(jid+"-source.mp4")
    if target.is_file() and expected_hash and _file_sha256(target)==expected_hash:
        martial._probe_video_file(target)
        return target
    if target.exists():
        target.unlink(missing_ok=True)
    # The same locked reference can feed teaching and practice jobs. Reuse the
    # verified local original instead of downloading the large file again.
    if expected_hash:
        for cached in folder.glob("mj_*-source.mp4"):
            if cached==target or not cached.is_file() or cached.is_symlink():
                continue
            if _file_sha256(cached)!=expected_hash:
                continue
            martial._probe_video_file(cached)
            os.link(cached,target)
            return target
    pending=target.with_suffix(".part")
    pending.unlink(missing_ok=True)
    req=urllib.request.Request(value,headers={"Accept":"video/mp4"},method="GET")
    size=0
    try:
        with urllib.request.build_opener(_NoRedirect).open(req,timeout=180) as response:
            content_length=response.headers.get("Content-Length")
            if content_length and int(content_length)>MAX_VIDEO_FILE:
                raise ValueError("动作参考原片超过 512MB")
            fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"wb") as output:
                while True:
                    chunk=response.read(1024*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>MAX_VIDEO_FILE:raise ValueError("动作参考原片超过 512MB")
                    output.write(chunk)
                output.flush();os.fsync(output.fileno())
        if not size:raise ValueError("动作参考原片为空")
        if expected_hash and _file_sha256(pending)!=expected_hash:
            raise ValueError("动作参考原片校验值与锁定素材不符")
        martial._probe_video_file(pending)
        pending.replace(target)
        return target
    finally:
        pending.unlink(missing_ok=True)


def _clip_source(source: Path, segment: dict, scratch: Path, jid: str, ffmpeg: str) -> dict:
    """Frame-accurate, bounded MP4 excerpt from the original source."""
    import martial
    try:
        index=int(segment["index"]);start=float(segment["source_start"]);end=float(segment["source_end"])
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError("动作参考裁剪区间无效") from exc
    if not 0<=index<20 or not all(math.isfinite(x) for x in (start,end)) or start<0 or not 0<end-start<=30:
        raise ValueError("动作参考裁剪区间超出供应商 30 秒输入范围")
    source_probe=martial._probe_video_file(source)
    source_duration=float(source_probe["duration"])
    if end>source_duration+0.05:raise ValueError("动作参考裁剪区间超出原片时长")
    folder=scratch/"reference-clips"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=folder/(jid+f"-{index:02d}.mp4")
    if target.is_file():
        try:
            size=target.stat().st_size
            actual=float(martial._probe_video_file(target)["duration"])
            if 0<size<=MAX_REFERENCE_CLIP_FILE and abs(actual-(end-start))<=0.35:
                return {"path":target,"duration":actual,"size":size,"sha256":_file_sha256(target)}
        except (OSError,ValueError,KeyError,TypeError):
            pass
        target.unlink(missing_ok=True)
    pending=target.with_suffix(".part.mp4")
    pending.unlink(missing_ok=True)
    portrait=source_probe["frame_orientation"]=="竖屏"
    video_filter="scale=480:-2,fps=24" if portrait else "scale=-2:480,fps=24"
    command=[ffmpeg,"-nostdin","-hide_banner","-loglevel","error","-ss",f"{start:.3f}",
             "-i",str(source),"-t",f"{end-start:.3f}","-map","0:v:0","-an","-c:v","libx264",
             "-vf",video_filter,"-preset","veryfast","-crf","20","-pix_fmt","yuv420p","-movflags","+faststart",
             "-y",str(pending)]
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=900,check=False)
        if result.returncode:raise RuntimeError("FFmpeg 裁剪失败："+result.stderr[-300:])
        size=pending.stat().st_size
        if not 0<size<=MAX_REFERENCE_CLIP_FILE:raise ValueError("动作参考裁剪片段为空或超过 200MB")
        actual=float(martial._probe_video_file(pending)["duration"])
        if abs(actual-(end-start))>0.35:
            raise ValueError(f"动作参考裁剪片段实长 {actual:.2f} 秒，与所选 {end-start:.2f} 秒不符")
        pending.replace(target)
        return {"path":target,"duration":actual,"size":size,"sha256":_file_sha256(target)}
    finally:
        pending.unlink(missing_ok=True)


def _upload_reference_clip(base_url: str, token: str, jid: str, segment: dict, clipped: dict) -> str:
    index=int(segment["index"])
    path=clipped["path"]
    marker=path.with_suffix(".upload.json")
    if marker.is_file():
        try:
            cached=json.loads(marker.read_text())
            value=_signed_media_url(cached["video_url"],base_url,f"/api/martial/clip/{jid}/{index}")
            expires=int((parse_qs(urlsplit(value).query).get("exp") or ["0"])[0])
            if cached["sha256"]==clipped["sha256"] and expires>int(time.time())+600:
                return value
        except (KeyError,TypeError,ValueError,OSError):
            pass
        marker.unlink(missing_ok=True)
    with path.open("rb") as stream:
        req=urllib.request.Request(base_url+f"/api/martial/connector/clips/{jid}/{index}",stream,
            headers={"Authorization":"Bearer "+token,"Content-Type":"video/mp4",
                     "Content-Length":str(clipped["size"]),"X-File-SHA256":clipped["sha256"]},method="POST")
        with urllib.request.build_opener(_NoRedirect).open(req,timeout=600) as response:
            body=json.load(response)
    if body.get("sha256")!=clipped["sha256"]:
        raise ValueError("工作台回传的裁剪片段校验值不一致")
    try:duration=float(body["duration"])
    except (KeyError,TypeError,ValueError) as exc:raise ValueError("工作台未确认裁剪片段实长") from exc
    if not math.isfinite(duration) or abs(duration-clipped["duration"])>0.35:
        raise ValueError("工作台回传的裁剪片段时长不一致")
    value=_signed_media_url(body.get("video_url"),base_url,f"/api/martial/clip/{jid}/{index}")
    expires=int((parse_qs(urlsplit(value).query).get("exp") or ["0"])[0])
    if expires<=int(time.time())+600:raise ValueError("工作台签发的裁剪片段链接即将过期")
    pending=marker.with_suffix(".tmp")
    pending.write_text(json.dumps({"sha256":clipped["sha256"],"video_url":value},ensure_ascii=False))
    pending.chmod(0o600);pending.replace(marker)
    return value


def _media_segments(spec: dict) -> list[dict]:
    """Accept only the exact priced segment specification from Work OS."""
    segments=spec.get("segments")
    if spec.get("generation_mode")=="preview" and not segments:
        plan=spec.get("video_plan") or {}
        segments=[{"index":0,"source_start":plan.get("source_start"),
                   "source_end":plan.get("source_end"),"duration":spec.get("duration"),
                   "reserved_cost":spec.get("reserved_cost"),"request_key":spec.get("request_key"),
                   "prompt":spec.get("prompt"),"use_original":True}]
    if not isinstance(segments,list) or not 1<=len(segments)<=20:
        raise ValueError("工作台未给出完整的分段生成计划")
    if spec.get("generation_mode") not in {"preview","complete"}:
        raise ValueError("视频生成方式未获批准")
    if spec.get("resolution") not in {"480p","720p","1080p"} or spec.get("ratio") not in {
        "16:9","9:16","1:1","4:3","3:4","21:9","9:21","adaptive"}:
        raise ValueError("视频画质或画幅不在已核价规格内")
    if not isinstance(spec.get("quote_source"),str) or not spec["quote_source"].strip():
        raise ValueError("视频报价来源缺失")
    try:
        target=int(spec["duration"]);reserved=float(spec["reserved_cost"])
    except (KeyError,TypeError,ValueError) as exc:raise ValueError("视频目标时长或预留费用无效") from exc
    if target<1 or not math.isfinite(reserved) or reserved<=0:raise ValueError("视频目标时长或预留费用无效")
    total_duration=0;total_cost=0.0;keys=set()
    for index,segment in enumerate(segments):
        if not isinstance(segment,dict) or segment.get("index")!=index:
            raise ValueError("视频分段索引缺失或顺序错误")
        try:
            duration=int(segment["duration"]);cost=float(segment["reserved_cost"])
            start=float(segment["source_start"]);end=float(segment["source_end"])
            key=segment["request_key"]
        except (KeyError,TypeError,ValueError) as exc:raise ValueError("视频分段时长、参考区间或报价缺失") from exc
        if (not 1<=duration<=30 or segment["duration"]!=duration or
            not math.isfinite(cost) or cost<=0 or
            not all(math.isfinite(x) for x in (start,end)) or start<0 or not 0<end-start<=30 or
            not isinstance(key,str) or not key or key in keys):
            raise ValueError("视频分段规格或幂等编号无效")
        keys.add(key);total_duration+=duration;total_cost+=cost
    if total_duration!=target or abs(total_cost-reserved)>0.011:
        raise ValueError("视频分段总时长或费用与核价单不一致")
    if spec["generation_mode"]=="preview" and (len(segments)!=1 or target!=5):
        raise ValueError("5 秒试拍片的分段规格无效")
    if spec["generation_mode"]=="complete" and spec.get("video_urls"):
        raise ValueError("完整视频任务禁止把原片直接作为供应商参考")
    if spec["generation_mode"]=="complete" and len(segments)<2 and target>30:
        raise ValueError("完整视频超过单次生成时长却没有分段")
    return segments


def _segment_intent(scratch: Path, jid: str, segment: dict) -> Path:
    folder=scratch/"media-intents"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    return folder/(jid+f"-{segment['index']:02d}.json")


def _write_segment_intent(path: Path, segment: dict, media_payload: dict) -> None:
    body={"request_key":segment["request_key"],
          "payload_sha256":hashlib.sha256(json.dumps(media_payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as stream:
        json.dump(body,stream,ensure_ascii=False)
        stream.flush();os.fsync(stream.fileno())


def _assemble_segments(paths: list[Path], segments: list[dict], spec: dict,
                       ledger_id: str, jid: str, ffmpeg: str) -> Path:
    """Join approved generated segments into the single candidate for QC."""
    import martial
    if not re.fullmatch(r"[a-f0-9]{16}",str(ledger_id)):
        raise ValueError("本地工作流账本编号无效")
    if len(paths)!=len(segments) or not paths:raise ValueError("完整视频分段输出不齐")
    target_duration=float((spec.get("video_plan") or {}).get("target_duration") or spec["duration"])
    if not math.isfinite(target_duration) or target_duration<1 or target_duration>float(spec["duration"]):
        raise ValueError("完整视频精确目标时长无效")
    paths=[path.resolve(strict=True) for path in paths]
    width=height=0
    for index,path in enumerate(paths):
        if not path.is_relative_to((WORKFLOW/"tasks").resolve()) or path.suffix.lower()!=".mp4":
            raise ValueError("分段生成文件不在工作流任务输出区")
        probe=martial._probe_video_file(path)
        if abs(float(probe["duration"])-float(segments[index]["duration"]))>1.0:
            raise ValueError("生成分段实长与已核价时长不符")
        if index==0:
            width=int(probe["width"]);height=int(probe["height"])
    width-=width%2;height-=height%2
    if width<2 or height<2:raise ValueError("生成分段画面尺寸无效")
    folder=WORKFLOW/"tasks"/ledger_id/"media"
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=folder/(jid+"-complete.mp4")
    if target.is_file():
        probe=martial._probe_video_file(target)
        if abs(float(probe["duration"])-target_duration)<=0.2:return target
        raise ValueError("已合成候选的视频时长与当前任务不一致")
    pending=folder/(jid+"-complete.part.mp4")
    pending.unlink(missing_ok=True)
    filters=[]
    for index in range(len(paths)):
        filters.append(f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                       f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
                       f"settb=AVTB,setpts=PTS-STARTPTS[v{index}]")
    if len(paths)>1:
        filters.append("".join(f"[v{i}]" for i in range(len(paths)))+
                       f"concat=n={len(paths)}:v=1:a=0[c]")
    else:
        filters.append("[v0]null[c]")
    filters.append(f"[c]tpad=stop_mode=clone:stop_duration=1,trim=duration={target_duration:.3f},"
                   "setpts=PTS-STARTPTS[out]")
    command=[ffmpeg,"-nostdin","-hide_banner","-loglevel","error"]
    for path in paths:command.extend(("-i",str(path)))
    command.extend(("-filter_complex",";".join(filters),"-map","[out]","-an","-c:v","libx264",
                    "-preset","veryfast","-crf","18","-pix_fmt","yuv420p",
                    "-movflags","+faststart","-f","mp4","-y",str(pending)))
    try:
        result=subprocess.run(command,capture_output=True,text=True,timeout=1800,check=False)
        if result.returncode:raise RuntimeError("FFmpeg 合成完整视频失败："+result.stderr[-300:])
        if not 0<pending.stat().st_size<=MAX_VIDEO_FILE:raise ValueError("合成完整视频为空或超过 512MB")
        actual=float(martial._probe_video_file(pending)["duration"])
        if abs(actual-target_duration)>0.2:
            raise ValueError("合成完整视频实长与目标时长不一致")
        pending.replace(target)
        return target
    finally:
        pending.unlink(missing_ok=True)


def _upload_video_file(url: str, token: str, jid: str, path: Path, sha256: str) -> dict:
    size=path.stat().st_size
    if not 0<size<=MAX_VIDEO_FILE:raise ValueError("生成视频为空或超过 512MB")
    with path.open("rb") as stream:
        req=urllib.request.Request(url+"/api/martial/connector/media/"+jid+"/video-file",stream,
            headers={"Authorization":"Bearer "+token,"Content-Type":"video/mp4",
                     "Content-Length":str(size),"X-File-SHA256":sha256},method="POST")
        with urllib.request.build_opener(_NoRedirect).open(req,timeout=600) as response:
            return json.load(response)


def _return_saved_video(url: str, token: str, jid: str, saved: dict) -> dict:
    """Retry transfer/report using the original local file; never re-submit Runy."""
    if "report" not in saved:return _report(url,token,"media",jid,saved)  # Old result cache.
    report=saved["report"]
    path=Path(saved["local_video_path"]).resolve(strict=True)
    if not path.is_relative_to((WORKFLOW/"tasks").resolve()) or path.suffix.lower()!=".mp4":
        raise ValueError("工作流返回的视频不在任务输出区")
    sha=report.get("video_sha256")
    if not isinstance(sha,str) or _file_sha256(path)!=sha:
        raise ValueError("本地生成文件校验值变化，暂停回传")
    _upload_video_file(url,token,jid,path,sha)
    return _report(url,token,"media",jid,report)


def run_once(config: dict):
    url=config["url"].rstrip("/")
    token=config["token"]
    scratch=Path(config["scratch"])/"martial"
    scratch.mkdir(parents=True,exist_ok=True)
    from video_routing import resolve
    routes=[]
    for alias in ("sd2.0","sd2.5"):
        try:route=resolve(alias)
        except ValueError:continue
        routes.append({"model_alias":alias,**route})
    request(url+"/api/martial/connector/routes/report",token,"POST",{"routes":routes})
    packages=request(url+"/api/martial/connector/packages",token)["jobs"]
    for item in packages:
        jid=item["id"]
        result_path=scratch/(jid+".result.json")
        if result_path.is_file():
            _report(url,token,"packages",jid,json.loads(result_path.read_text()))
            result_path.unlink()
            continue
        if item["status"]=="dispatching":
            _report(url,token,"packages",jid,{"status":"unknown_submission","error":"本机 DeepSeek 调用中断；不自动重复请求"})
            continue
        claimed=request(url+"/api/martial/connector/packages/"+jid+"/claim",token,"POST",{})
        try:report=_package(claimed["facts"])
        except Exception as exc:
            report={"status":"unknown_submission" if type(exc).__name__=="UnknownSubmission" else "failed",
                    "error":type(exc).__name__+": "+str(exc)[:260]}
        result_path.write_text(json.dumps(report,ensure_ascii=False))
        result_path.chmod(0o600)
        _report(url,token,"packages",jid,report)
        result_path.unlink()

    media=request(url+"/api/martial/connector/media",token)["jobs"]
    for item in media:
        jid=item["id"]
        result_path=scratch/(jid+".video-result.json")
        if result_path.is_file():
            _return_saved_video(url,token,jid,json.loads(result_path.read_text()))
            result_path.unlink()
            continue
        spec=request(url+"/api/martial/connector/media/"+jid+"/claim",token,"POST",{})
        submitted_attempt=False
        active_key=""
        ledger_id=spec.get("local_job_id")
        local_media_id=spec.get("local_media_id")
        upstream=""
        progress=[]
        try:
            if spec["model_alias"] not in {"sd2.0","sd2.5"} or spec["provider"]!="runy":
                raise ValueError("视频路由未获批准")
            segments=_media_segments(spec)
            progress=[{"index":s["index"],"source_start":s["source_start"],
                       "source_end":s["source_end"],"status":"queued"} for s in segments]
            prior=spec.get("segment_progress") or []
            if (isinstance(prior,list) and len(prior)==len(progress) and
                all(isinstance(p,dict) and p.get("index")==i for i,p in enumerate(prior))):
                for index,p in enumerate(prior):
                    if p.get("status") in {"clip_ready","submitted","running","downloaded","complete"}:
                        progress[index]["status"]=p["status"]
            route=resolve(spec["model_alias"],provider_name=spec["provider"])
            if route!={"provider":spec["provider"],"model":spec["model"]}:
                raise ValueError("历史任务的平台或模型无效")
            if spec["status"]=="queued" and resolve(spec["model_alias"])!=route:
                raise ValueError("当前平台路由已变化；新任务未提交供应商")
            ffmpeg=_ffmpeg_binary(config) if spec["generation_mode"]=="complete" else None
            ledger_id=spec.get("local_job_id")
            if not ledger_id:
                job=workflow_request("POST","/api/jobs",{
                    "brief":"Work OS 武学数字资产视频生成账本 "+jid,
                    "project":"万象武境编剧","kind":"general","request_key":"workos:martial:ledger:"+jid,
                    "mode":"ledger","max_steps":1,"max_output_tokens":128,
                    "capability_budget_cny":0,"capability_max_calls":0})
                ledger_id=job["id"]
            saved_rows={s["index"]:_saved_workflow_media(s["request_key"]) for s in segments}
            source=None
            prepared_urls={}

            def prepare_reference(segment):
                nonlocal source
                if int(time.time())>=int(spec["reference_expires_at"])-60:
                    raise ValueError("角色或动作参考链接已过期；本次任务未提交供应商")
                if source is None:source=_download_source(spec,url,scratch,jid)
                if segment.get("use_original"):
                    import martial
                    length=float(martial._probe_video_file(source)["duration"])
                    if (length>30 or abs(float(segment["source_start"]))>0.01 or
                        abs(float(segment["source_end"])-length)>0.05):
                        raise ValueError("5 秒样片不能传送超长或未裁切的整条原片")
                    refs=spec.get("video_urls") or []
                    if len(refs)!=1:raise ValueError("5 秒样片缺少原片参考链接")
                    return _signed_media_url(refs[0],url,"/api/martial/source/")
                clipped=_clip_source(source,segment,scratch,jid,ffmpeg)
                return _upload_reference_clip(url,token,jid,segment,clipped)

            # For a fresh complete job, prepare and upload every short reference
            # before charging for segment one. A bad later clip blocks all POSTs.
            if not any(saved_rows.values()) and (spec["status"]=="queued" or spec["generation_mode"]=="complete") and all(
                    not _segment_intent(scratch,jid,s).exists() for s in segments):
                for segment in segments:
                    prepared_urls[segment["index"]]=prepare_reference(segment)
                    progress[segment["index"]]["status"]="clip_ready"

            rows=[];paths=[];stopped=False
            for segment in segments:
                index=segment["index"]
                saved=saved_rows[index]
                intent=_segment_intent(scratch,jid,segment)
                if saved:
                    media_row=workflow_request("GET","/api/media?"+urlencode({"id":saved["id"]}))
                elif intent.exists() or (index==0 and spec["status"]!="queued" and
                                         spec["generation_mode"]=="preview"):
                    # A durable intent can precede the HTTP reply. Never issue a
                    # second POST without finding its Company Workflow ledger row.
                    saved=_settled_workflow_media(segment["request_key"])
                    if not saved:
                        progress[index]["status"]="unknown_submission"
                        _report(url,token,"media",jid,{"status":"unknown_submission",
                            "error":"该分段原请求没有可核实的账本结果；须核对润元任务/账单，不自动重提",
                            "local_job_id":ledger_id,"segment_progress":progress})
                        stopped=True;break
                    media_row=workflow_request("GET","/api/media?"+urlencode({"id":saved["id"]}))
                else:
                    reference_url=prepared_urls.get(index) or prepare_reference(segment)
                    progress[index]["status"]="clip_ready"
                    payload={"job_id":ledger_id,"kind":"video","prompt":segment["prompt"],
                             "request_key":segment["request_key"],
                             "quoted_cost_cny":segment["reserved_cost"],"budget_cny":spec["reserved_cost"],
                             "quote_source":spec["quote_source"],"model":spec["model_alias"],
                             "provider_name":spec["provider"],"duration":segment["duration"],
                             "resolution":spec["resolution"],"ratio":spec["ratio"],"generate_audio":False,
                             "image_urls":spec["image_urls"],"video_urls":[reference_url]}
                    _write_segment_intent(intent,segment,payload)
                    active_key=segment["request_key"];submitted_attempt=True
                    media_row=_submit_workflow_media(payload)
                    submitted_attempt=False
                local_media_id=media_row["id"]
                status=media_row["status"]
                upstream=str(media_row.get("upstream_id") or "")
                if status=="submitting" and not upstream:
                    progress[index]["status"]="unknown_submission"
                    _report(url,token,"media",jid,{"status":"unknown_submission",
                        "error":"原润元提交仍无任务编号；须核对平台和账单，不自动重发",
                        "local_job_id":ledger_id,"local_media_id":local_media_id,"segment_progress":progress})
                    stopped=True;break
                if status=="uncertain" and upstream:
                    try:
                        media_row=workflow_request("GET","/api/media?"+urlencode({"id":local_media_id,"refresh":"true"}))
                        status=media_row["status"]
                        upstream=str(media_row.get("upstream_id") or upstream)
                    except Exception:pass
                if status=="uncertain":
                    progress[index]["status"]="unknown_submission"
                    _report(url,token,"media",jid,{"status":"unknown_submission",
                        "error":"该分段供应商提交结果未知；按原任务/账单核对，不自动重发",
                        "local_job_id":ledger_id,"local_media_id":local_media_id,
                        "provider_job_id":upstream,"segment_progress":progress})
                    stopped=True;break
                if status in {"rejected","failed","expired","cancelled"}:
                    progress[index]["status"]="failed"
                    _report(url,token,"media",jid,{"status":"failed",
                        "error":str((media_row.get("response") or {}).get("error") or status)[:300],
                        "local_job_id":ledger_id,"local_media_id":local_media_id,
                        "provider_job_id":upstream,"segment_progress":progress})
                    stopped=True;break
                if status not in {"succeeded","success","done","completed"}:
                    progress[index]["status"]="running" if status=="running" else "submitted"
                    _report(url,token,"media",jid,{"status":"running" if status in {"running","queued"} else "submitted",
                        "local_job_id":ledger_id,"local_media_id":local_media_id,
                        "provider_job_id":upstream,"segment_progress":progress})
                    stopped=True;break
                files=(media_row.get("response") or {}).get("local_files") or []
                if not files:
                    progress[index]["status"]="running"
                    _report(url,token,"media",jid,{"status":"download_pending","local_job_id":ledger_id,
                        "local_media_id":local_media_id,"provider_job_id":upstream,"segment_progress":progress})
                    stopped=True;break
                path=Path(files[0]).resolve(strict=True)
                if not path.is_relative_to((WORKFLOW/"tasks").resolve()) or path.suffix.lower()!=".mp4":
                    raise ValueError("工作流返回的视频不在任务输出区")
                if not 0<path.stat().st_size<=MAX_VIDEO_FILE:raise ValueError("生成视频为空或超过 512MB")
                progress[index]["status"]="downloaded"
                rows.append(media_row);paths.append(path)
            if stopped:continue
            if spec["generation_mode"]=="complete":
                path=_assemble_segments(paths,segments,spec,ledger_id,jid,ffmpeg)
            else:path=paths[0]
            _report(url,token,"media",jid,{"status":"technical_check","local_job_id":ledger_id,
                    "local_media_id":local_media_id,"provider_job_id":upstream,"segment_progress":progress})
            for part in progress:part["status"]="complete"
            billings=[(r.get("billing") or {}).get("actual_cost_cny") for r in rows]
            actual_cost=sum(float(x) for x in billings) if all(x is not None for x in billings) else None
            report={"status":"succeeded","video_sha256":_file_sha256(path),
                    "technical":_technical(path),"local_job_id":ledger_id,"local_media_id":local_media_id,
                    "provider_job_id":upstream,"actual_cost":actual_cost,"segment_progress":progress,
                    "segment_media_ids":[r["id"] for r in rows]}
            saved={"report":report,"local_video_path":str(path)}
            pending=result_path.with_suffix(".tmp")
            fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"w") as output:
                json.dump(saved,output,ensure_ascii=False)
                output.flush();os.fsync(output.fileno())
            pending.replace(result_path)
            _return_saved_video(url,token,jid,saved)
            result_path.unlink()
        except Exception as exc:
            # A failure after an upstream POST may be ambiguous. Company Workflow's
            # request_key prevents duplicate submission; retain the job for audit.
            if result_path.is_file():
                print("武学视频结果已暂存，下次重试回传："+type(exc).__name__,flush=True)
            elif submitted_attempt:
                try:saved=_settled_workflow_media(active_key)
                except Exception:saved=None
                if saved and saved.get("upstream_id"):
                    # The original request reached Company Workflow. Its worker
                    # can poll the saved Runy task, so keep this job resumable.
                    _report(url,token,"media",jid,{"status":"submitted",
                        "local_job_id":ledger_id,"local_media_id":saved["id"],
                        "provider_job_id":saved["upstream_id"],"segment_progress":progress})
                    continue
                if saved and saved.get("status")=="rejected":
                    _report(url,token,"media",jid,{"status":"failed",
                        "error":"原请求被明确拒绝；未重新提交",
                        "local_job_id":ledger_id,"local_media_id":saved["id"],"segment_progress":progress})
                    continue
                _report(url,token,"media",jid,{"status":"unknown_submission",
                    "error":type(exc).__name__+": "+str(exc)[:260],
                    "local_job_id":ledger_id,"local_media_id":(saved or {}).get("id") or local_media_id,
                    "provider_job_id":upstream,"segment_progress":progress})
            elif spec.get("status") in {"queued","dispatching"}:
                _report(url,token,"media",jid,{"status":"failed",
                    "error":type(exc).__name__+": "+str(exc)[:260],
                    "local_job_id":ledger_id,"local_media_id":local_media_id,"segment_progress":progress})
            else:
                print("武学视频状态检查暂不可用："+type(exc).__name__+" "+str(exc)[:140],flush=True)

    # The existing LaunchAgent already runs this connector. Keep the company
    # AI Studio queue on that proven schedule without a second daemon or key.
    try:
        from ai_studio_connector import run_once as run_ai_studio_once
        run_ai_studio_once(config)
    except (urllib.error.URLError, KeyError, ValueError, RuntimeError, OSError) as exc:
        print("AI 创作连接器暂不可用："+type(exc).__name__+" "+str(exc)[:140],flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    if not config["url"].startswith("https://"):raise SystemExit("连接器只允许 HTTPS Work OS")
    while True:
        try:run_once(config)
        except (urllib.error.URLError,KeyError,ValueError,RuntimeError,OSError) as exc:
            print("武学连接器暂不可用："+type(exc).__name__+" "+str(exc)[:180],flush=True)
        if args.once:break
        time.sleep(8)


if __name__=="__main__":main()

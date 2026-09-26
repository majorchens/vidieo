"""Use Company Workflow's proven Wanjie Chat client, explicitly selecting DeepSeek."""
from __future__ import annotations

import json
import sys
from pathlib import Path

WORKFLOW_ROOT=Path("/Users/majorchen/Documents/Codex/2026-09-18/new-chat/outputs/company-workflow")
sys.path.insert(0,str(WORKFLOW_ROOT/"app"))


def analyze(spec: dict, kind: str) -> dict:
    if kind not in {"prepare","precheck","copilot"} or not spec.get("units"):
        raise ValueError("无效 DeepSeek 工作包")
    import provider
    system=(
        "你是 Yoodun Work OS 的执行分析模型 deepseek-v4.1-flash。只输出一个合法 JSON 对象，"
        "不要 Markdown、代码围栏或额外文字。只依据给定文本及资产登记事实，不声称看过未提供的图片或视频。"
        "二进制资产已在 Work OS 中提供给员工，模型不能直接查看不等于员工缺素材；把画面／动作核对写入 qc_checklist，"
        "missing_inputs 只写真正缺失的业务资料或未核实的关键事实。不要虚构已发布或已付费生成。"
    )
    if kind=="copilot":
        system += "你是员工的任务助手。只解释、指导和建议；不能改变定版角色/动作、预算、供应商、知识版本、任务状态或验收结论。需要管理者决定时标记 needs_manager=true。回答简短、具体。"
    brief=spec["units"][0]["brief"]
    response=provider.chat([{"role":"system","content":system},{"role":"user","content":brief}],max_tokens=3200 if kind=="prepare" else 900 if kind=="copilot" else 1600,final=True,thinking="disabled",model="deepseek-v4.1-flash")
    if response.get("model") and response["model"]!="deepseek-v4.1-flash":
        raise RuntimeError("万界返回了非指定模型")
    choices=response.get("choices") or []
    content=choices[0].get("message",{}).get("content") if choices else None
    if not isinstance(content,str):raise RuntimeError("DeepSeek 未返回文本结果")
    cleaned=content.strip()
    if cleaned.startswith("```json") and cleaned.endswith("```"):
        cleaned=cleaned[7:-3].strip()
    try:body=json.loads(cleaned)
    except ValueError as exc:raise RuntimeError("DeepSeek 返回的工作包不是 JSON；不自动重复付费调用") from exc
    if not isinstance(body,dict):raise RuntimeError("DeepSeek 工作包不是 JSON 对象")
    return {"status":"complete","plan_id":str(response.get("id") or ""),"final_job":str(response.get("id") or ""),"body":body,"usage":response.get("usage") or {},"model":response.get("model") or "deepseek-v4.1-flash"}

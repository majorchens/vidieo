"""Mac-side transport for Work OS jobs. Company Workflow remains the executor."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from direct_deepseek import analyze


def request(url: str, token: str, method="GET", payload=None):
    body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode()
    req=urllib.request.Request(url,body,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"},method=method)
    with urllib.request.urlopen(req,timeout=35) as response:
        return json.load(response)


def run_once(config: dict):
    url=config["url"].rstrip("/")
    token=config["token"]
    scratch=Path(config["scratch"]);scratch.mkdir(parents=True,exist_ok=True)
    jobs=request(url+"/api/connector/jobs",token)["jobs"]
    for job in jobs:
        jid=job["id"]
        result_path=scratch/(jid+".result.json")
        if result_path.is_file():
            report=json.loads(result_path.read_text())
            request(url+"/api/connector/jobs/"+jid+"/report",token,"POST",report)
            result_path.unlink()
            continue
        if job["status"]=="dispatching":
            # A process loss after the provider request has an unknown outcome.
            request(url+"/api/connector/jobs/"+jid+"/report",token,"POST",{"status":"unknown_submission","error":"本机调用中断，未自动重发万界请求"})
            continue
        if job["status"]=="queued":
            claimed=request(url+"/api/connector/jobs/"+jid+"/claim",token,"POST",{})
            spec=claimed["spec"]
            if spec.get("request_key")!=job["request_key"] or spec.get("project") not in {"万象武境编剧","钓江湖"}:
                request(url+"/api/connector/jobs/"+jid+"/report",token,"POST",{"status":"failed","error":"请求不在已批准的项目范围"})
                continue
            try:
                report=analyze(spec,job["kind"])
            except Exception as exc:
                status="unknown_submission" if type(exc).__name__=="UnknownSubmission" else "failed"
                report={"status":status,"error":str(exc)[:300]}
            temp=result_path.with_suffix(".tmp")
            fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"w") as stream:
                json.dump(report,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            temp.replace(result_path)
            request(url+"/api/connector/jobs/"+jid+"/report",token,"POST",report)
            result_path.unlink()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",required=True)
    parser.add_argument("--once",action="store_true")
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    if not config["url"].startswith("https://"):
        raise SystemExit("连接器只允许 HTTPS Work OS")
    while True:
        try:run_once(config)
        except (urllib.error.URLError,KeyError,ValueError,RuntimeError) as exc:
            print("连接器暂不可用："+type(exc).__name__+" "+str(exc)[:180],flush=True)
        if args.once:break
        time.sleep(8)


if __name__=="__main__":main()

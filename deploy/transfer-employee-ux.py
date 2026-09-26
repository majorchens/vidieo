#!/usr/bin/env python3
"""Transfer one verified, code-only Work OS release from Cloud Shell to ECS."""
import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

INSTANCE = "i-bp1bmlur2j43xbciix2s"
REGION = "cn-hangzhou"
REMOTE_PARTS = "/tmp/yoodun-employee-assets-parts"
REMOTE_ARCHIVE = "/tmp/yoodun-work-os-employee-assets-20260924.tar.gz"
CHUNK_SIZE = 12000


def aliyun(operation, *args):
    result = subprocess.run(["aliyun", "ecs", operation, "--RegionId", REGION, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, check=False)
    if result.returncode:
        raise RuntimeError(operation + " failed: " + result.stderr.strip()[:500])
    return json.loads(result.stdout)


def run_remote(script, token, timeout=120):
    content = base64.b64encode(script.encode()).decode()
    if len(content) > 23000:
        raise ValueError("remote command exceeds conservative size limit")
    result = aliyun("RunCommand", "--Type", "RunShellScript", "--ContentEncoding", "Base64",
                    "--CommandContent", content, "--InstanceId.1", INSTANCE,
                    "--ClientToken", token, "--Timeout", str(timeout))
    for _ in range(timeout // 2 + 20):
        time.sleep(2)
        status = aliyun("DescribeInvocationResults", "--InvokeId", result["InvokeId"])
        rows = status.get("Invocation", {}).get("InvocationResults", {}).get("InvocationResult", [])
        if not rows:
            continue
        row = rows[0]
        state = row.get("InvocationStatus")
        if state in ("Success", "Failed", "Stopped"):
            output = base64.b64decode(row.get("Output") or "").decode(errors="replace")
            if state != "Success" or row.get("ExitCode") != 0:
                raise RuntimeError("remote command " + token + ": " + state + ", " + output[:500])
            return output.strip()
    raise TimeoutError("remote command did not finish within the requested timeout")


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in {"transfer","deploy","report","cleanup"}:
        raise SystemExit("usage: transfer-employee-ux.py transfer|deploy|report|cleanup ARCHIVE EXPECTED_SHA256")
    mode = sys.argv[1]
    source = Path(sys.argv[2])
    expected = sys.argv[3]
    if mode == "deploy":
        command = (
            "set -eu; archive={archive}; "
            "test \"$(sha256sum $archive | cut -d' ' -f1)\" = {sha}; "
            "tar -xOzf $archive deploy/upgrade-employee-ux.sh > /tmp/yoodun-upgrade-employee-ux.sh; "
            "chmod 700 /tmp/yoodun-upgrade-employee-ux.sh; "
            "sh /tmp/yoodun-upgrade-employee-ux.sh $archive {sha}"
        ).format(archive=REMOTE_ARCHIVE,sha=expected)
        print(run_remote(command,"yoodun-employee-assets-deploy-"+expected[:12],timeout=900),flush=True)
        return
    if mode == "report":
        command=("python3 - <<'PY'\nimport json\nfrom pathlib import Path\n"
                 "p=Path('/var/lib/yoodun-work-os/reports/Legacy_AI_Asset_Migration_Report.json')\n"
                 "data=json.loads(p.read_text())\n"
                 "print(json.dumps({k:v for k,v in data.items() if k!='unmapped_assets'},ensure_ascii=False))\n"
                 "print('unmapped_count',len(data.get('unmapped_assets',[])))\nPY")
        print(run_remote(command,"yoodun-employee-assets-report-"+expected[:12]),flush=True)
        return
    if mode == "cleanup":
        command="set -eu; rm -r {parts}; rm {archive} /tmp/yoodun-upgrade-employee-ux.sh".format(
            parts=REMOTE_PARTS+"/"+expected[:12],archive=REMOTE_ARCHIVE)
        print(run_remote(command,"yoodun-employee-assets-cleanup-"+expected[:12]),flush=True)
        return
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Cloud Shell archive hash mismatch")
    parts_dir = REMOTE_PARTS + "/" + expected[:12]
    chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
    print("Source verified: {} bytes, {} chunks".format(len(data), len(chunks)), flush=True)
    for index, chunk in enumerate(chunks):
        payload = base64.b64encode(chunk).decode()
        remote = "set -eu; install -d -m 700 {0}; printf '%s' '{1}' > {0}/{2:03}.b64".format(
            parts_dir, payload, index)
        run_remote(remote, "yoodun-employee-assets-{}-{:03}".format(expected[:12], index))
        print("Chunk {}/{} transferred".format(index + 1, len(chunks)), flush=True)
    finalize = (
        "set -eu; cat {parts}/[0-9][0-9][0-9].b64 | base64 -d > {archive}; "
        "test \"$(sha256sum {archive} | cut -d' ' -f1)\" = {sha}; "
        "stat -c '%s bytes verified' {archive}"
    ).format(parts=parts_dir, archive=REMOTE_ARCHIVE, sha=expected)
    print(run_remote(finalize, "yoodun-employee-assets-assemble-" + expected[:12]), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("TRANSFER FAILED: " + str(exc), file=sys.stderr)
        raise SystemExit(1)

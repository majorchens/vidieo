#!/usr/bin/env python3
"""Move one verified code archive from Aliyun Cloud Shell to the existing ECS.

Cloud Assistant has a small per-command payload limit, so the archive is sent
in numbered chunks and verified again on the instance before installation.
No database, credential, or application media is included in this transfer.
"""

import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path


INSTANCE = "i-bp1bmlur2j43xbciix2s"
REGION = "cn-hangzhou"
REMOTE_PARTS = "/tmp/yoodun-ai-studio-parts"
REMOTE_ARCHIVE = "/tmp/yoodun-work-os-martial-ai-studio-20260924.tar.gz"
CHUNK_SIZE = 12000


def aliyun(operation, *args):
    result = subprocess.run(
        ["aliyun", "ecs", operation, "--RegionId", REGION, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(operation + " failed: " + result.stderr.strip()[:500])
    return json.loads(result.stdout)


def run_remote(script, token):
    content = base64.b64encode(script.encode()).decode()
    if len(content) > 23000:
        raise ValueError("remote command exceeds conservative size limit")
    result = aliyun(
        "RunCommand", "--Type", "RunShellScript", "--ContentEncoding", "Base64",
        "--CommandContent", content, "--InstanceId.1", INSTANCE,
        "--ClientToken", token, "--Timeout", "120",
    )
    for _ in range(40):
        time.sleep(1)
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
    raise TimeoutError("remote command did not finish in 40 seconds")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: transfer-ai-studio.py ARCHIVE EXPECTED_SHA256")
    source = Path(sys.argv[1])
    expected = sys.argv[2]
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("Cloud Shell archive hash mismatch")
    chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
    print("Source verified: {} bytes, {} chunks".format(len(data), len(chunks)), flush=True)
    for index, chunk in enumerate(chunks):
        payload = base64.b64encode(chunk).decode()
        remote = "set -eu; install -d -m 700 {0}; printf '%s' '{1}' > {0}/{2:03}.b64".format(
            REMOTE_PARTS, payload, index)
        run_remote(remote, "yoodun-ai-studio-{}-{:03}".format(expected[:12], index))
        print("Chunk {}/{} transferred".format(index + 1, len(chunks)), flush=True)
    finalize = (
        "set -eu; cat {parts}/[0-9][0-9][0-9].b64 | base64 -d > {archive}; "
        "test \"$(sha256sum {archive} | cut -d' ' -f1)\" = {sha}; "
        "stat -c '%s bytes verified' {archive}"
    ).format(parts=REMOTE_PARTS, archive=REMOTE_ARCHIVE, sha=expected)
    print(run_remote(finalize, "yoodun-ai-studio-assemble-" + expected[:12]), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("TRANSFER FAILED: " + str(exc), file=sys.stderr)
        raise SystemExit(1)

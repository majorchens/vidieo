#!/usr/bin/env python3
"""Transfer one verified Work OS patch via Alibaba ECS RunCommand."""
import base64
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

INSTANCE = "i-bp1bmlur2j43xbciix2s"
REGION = "cn-hangzhou"
REMOTE_ARCHIVE = "/tmp/yoodun-mabu-reference-patch.tar.gz"
REMOTE_PARTS = "/tmp/yoodun-mabu-reference-parts"
CHUNK_SIZE = 12_000


def aliyun(operation: str, *args: str) -> dict:
    result = subprocess.run(
        ["aliyun", "ecs", operation, "--RegionId", REGION, *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(operation + " failed: " + result.stderr.strip()[:400])
    return json.loads(result.stdout)


def run_remote(script: str, token: str, timeout: int = 120) -> str:
    content = base64.b64encode(script.encode()).decode()
    if len(content) > 23_000:
        raise ValueError("remote command is too large")
    invoked = aliyun(
        "RunCommand", "--Type", "RunShellScript", "--ContentEncoding", "Base64",
        "--CommandContent", content, "--InstanceId.1", INSTANCE,
        "--ClientToken", token, "--Timeout", str(timeout),
    )
    for _ in range(timeout // 2 + 20):
        time.sleep(2)
        result = aliyun("DescribeInvocationResults", "--InvokeId", invoked["InvokeId"])
        rows = result.get("Invocation", {}).get("InvocationResults", {}).get("InvocationResult", [])
        if not rows:
            continue
        row = rows[0]
        if row.get("InvocationStatus") in ("Success", "Failed", "Stopped"):
            output = base64.b64decode(row.get("Output") or "").decode(errors="replace")
            if row.get("InvocationStatus") != "Success" or row.get("ExitCode") != 0:
                raise RuntimeError("remote command failed: " + output[:500])
            return output.strip()
    raise TimeoutError("remote command did not finish")


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] not in {"transfer", "deploy", "cleanup"}:
        raise SystemExit("usage: transfer-mabu-reference.py transfer|deploy|cleanup ARCHIVE SHA256")
    mode, archive_name, expected = sys.argv[1:]
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("invalid expected SHA-256")
    source = Path(archive_name)
    if mode == "deploy":
        command = (
            "set -eu; archive={archive}; "
            "test \"$(sha256sum $archive | cut -d' ' -f1)\" = {sha}; "
            "tar -xOzf $archive deploy/upgrade-mabu-reference-patch.sh > /tmp/yoodun-upgrade-mabu-reference.sh; "
            "chmod 700 /tmp/yoodun-upgrade-mabu-reference.sh; "
            "sh /tmp/yoodun-upgrade-mabu-reference.sh $archive {sha}"
        ).format(archive=REMOTE_ARCHIVE, sha=expected)
        print(run_remote(command, "yoodun-mabu-deploy-" + expected[:12], timeout=900), flush=True)
        return
    if mode == "cleanup":
        command = "rm -rf {parts} {archive} /tmp/yoodun-upgrade-mabu-reference.sh".format(
            parts=REMOTE_PARTS + "/" + expected[:12], archive=REMOTE_ARCHIVE,
        )
        print(run_remote(command, "yoodun-mabu-clean-" + expected[:12]), flush=True)
        return
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Cloud Shell archive checksum mismatch")
    directory = REMOTE_PARTS + "/" + expected[:12]
    chunks = [data[pos:pos + CHUNK_SIZE] for pos in range(0, len(data), CHUNK_SIZE)]
    print("Source verified: {} bytes, {} chunks".format(len(data), len(chunks)), flush=True)
    for index, chunk in enumerate(chunks):
        payload = base64.b64encode(chunk).decode()
        script = "set -eu; install -d -m 700 {0}; printf '%s' '{1}' > {0}/{2:03}.b64".format(
            directory, payload, index,
        )
        run_remote(script, "yoodun-mabu-{}-{:03}".format(expected[:12], index))
        print("Chunk {}/{} transferred".format(index + 1, len(chunks)), flush=True)
    command = (
        "set -eu; cat {parts}/[0-9][0-9][0-9].b64 | base64 -d > {archive}; "
        "test \"$(sha256sum {archive} | cut -d' ' -f1)\" = {sha}; "
        "stat -c '%s bytes verified' {archive}"
    ).format(parts=directory, archive=REMOTE_ARCHIVE, sha=expected)
    print(run_remote(command, "yoodun-mabu-assemble-" + expected[:12]), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("TRANSFER FAILED: " + str(exc), file=sys.stderr)
        raise SystemExit(1)

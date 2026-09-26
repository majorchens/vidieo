#!/usr/bin/env python3
"""Import one selected Creative Lab media file into the shared cloud Asset Center.

Uses a normal Work OS login entered interactively. No credentials or media go
into Git, and import alone does not authorize formal lesson use.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def post(url: str, payload: dict, *, cookie: str = "", csrf: str = "") -> tuple[dict, str]:
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
        headers["X-CSRF-Token"] = csrf
    request = Request(url, json.dumps(payload, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            return json.load(response), response.headers.get("Set-Cookie", "")
    except HTTPError as exc:
        try:message = json.load(exc).get("error", f"HTTP {exc.code}")
        except (ValueError, OSError):message = f"HTTP {exc.code}"
        raise RuntimeError(message) from None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://ai.duodianqian.cn/work-os")
    parser.add_argument("--username", required=True)
    parser.add_argument("--project-id", choices=["wuxiang", "diaojianghu"], required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--source-ref", required=True,
                        help="relative path beginning Creative Lab/")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--learning-review-ref", help="manager-approved Learning Review path")
    parser.add_argument("--review-notes", help="specific human review evidence")
    args = parser.parse_args()
    if not args.file.is_file():parser.error("selected file does not exist")
    if args.file.stat().st_size > 40_000_000:parser.error("file exceeds 40MB")
    if bool(args.learning_review_ref) != bool(args.review_notes):
        parser.error("promotion requires both --learning-review-ref and --review-notes")
    base = args.base_url.rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1:"):
        parser.error("Work OS endpoint must use HTTPS or local loopback")
    password = getpass.getpass("Work OS password: ")
    auth, session = post(base + "/api/login", {"username": args.username, "password": password})
    del password
    cookie = session.split(";", 1)[0]
    if not cookie.startswith("yoodun_session="):
        raise RuntimeError("Work OS login did not return a session")
    contents = args.file.read_bytes()
    result, _ = post(base + "/api/asset-center/import-creative-lab", {
        "project_id": args.project_id, "experiment_id": args.experiment_id,
        "source_ref": args.source_ref, "expected_sha256": hashlib.sha256(contents).hexdigest(),
        "upload": {"name": args.file.name, "base64": base64.b64encode(contents).decode()},
    }, cookie=cookie, csrf=auth["csrf"])
    asset_id = result["asset"]["asset_id"]
    if args.learning_review_ref:
        post(base + f"/api/asset-center/{asset_id}/promote", {
            "learning_review_ref": args.learning_review_ref, "notes": args.review_notes,
        }, cookie=cookie, csrf=auth["csrf"])
    print(json.dumps({"asset_id": asset_id, "project_id": args.project_id,
                      "sha256": hashlib.sha256(contents).hexdigest(),
                      "production_reviewed": bool(args.learning_review_ref)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

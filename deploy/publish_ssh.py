#!/usr/bin/env python3
"""Package Work OS app code, copy it to ECS, and invoke the fixed deploy gate."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path

from ssh_release import ALLOWED_SUFFIXES, REQUIRED, valid_member_name, validate_archive


PROJECT = Path(__file__).resolve().parents[1]
REMOTE_DEPLOYER = "/usr/local/sbin/yoodun-work-os-deploy"


def package_app(target: Path) -> int:
    app = PROJECT / "app"
    files = []
    for path in app.rglob("*"):
        relative = path.relative_to(PROJECT).as_posix()
        if any(part == "__pycache__" or part.startswith(".") or part.endswith(".dist-info")
               for part in path.relative_to(PROJECT).parts):
            continue
        if path.is_dir() and not path.is_symlink():
            continue
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"non-regular application file: {relative}")
        if path.suffix.lower() not in ALLOWED_SUFFIXES or not valid_member_name(relative):
            continue
        files.append((relative, path))
    if not REQUIRED <= {name for name, _ in files}:
        raise ValueError("required application entries are missing")
    with target.open("xb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as bundle:
                for name, path in sorted(files):
                    info = tarfile.TarInfo(name)
                    info.size = path.stat().st_size
                    info.mode = 0o644
                    info.uid = info.gid = info.mtime = 0
                    with path.open("rb") as source:
                        bundle.addfile(info, source)
    return len(files)


def call(argv: list[str]) -> None:
    subprocess.run(argv, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="yoodun-workos-deploy")
    parser.add_argument("--identity", type=Path,
                        default=Path.home() / ".ssh/yoodun_workos_deploy_ed25519")
    parser.add_argument("--apply", action="store_true",
                        help="deploy after server-side read-only check")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9.-]+", args.host):
        parser.error("invalid host")
    if not args.identity.is_file():
        parser.error("SSH identity file is missing")
    remote = f"yoodun-deploy@{args.host}"
    ssh = ["ssh", "-i", str(args.identity), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
    with tempfile.TemporaryDirectory(prefix="yoodun-release-") as temp:
        draft = Path(temp) / "package.tar.gz"
        count = package_app(draft)
        digest = hashlib.sha256(draft.read_bytes()).hexdigest()
        name = f"release-{digest}.tar.gz"
        archive = draft.rename(draft.with_name(name))
        validate_archive(archive, digest)
        print(f"Package: {count} app files, SHA-256 {digest}", flush=True)
        remote_path = f"/home/yoodun-deploy/incoming/{name}"
        call(["scp", "-i", str(args.identity), "-o", "IdentitiesOnly=yes",
              "-o", "BatchMode=yes", str(archive), f"{remote}:{remote_path}"])
        check = f"sudo -n {REMOTE_DEPLOYER} --check {name} {digest}"
        call(ssh + [remote, check])
        if args.apply:
            apply = f"sudo -n {REMOTE_DEPLOYER} --apply {name} {digest}"
            call(ssh + [remote, apply])
        else:
            print("Server check passed; add --apply to deploy.")


if __name__ == "__main__":
    main()

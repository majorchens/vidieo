#!/usr/bin/env python3
"""Fixed, root-owned Work OS release gate for the yoodun-deploy SSH account.

Install this file once as /usr/local/sbin/yoodun-work-os-deploy. Never run a
script supplied inside an uploaded archive with root privileges.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import pwd
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


DEPLOY_USER = "yoodun-deploy"
INCOMING = Path("/home/yoodun-deploy/incoming")
RELEASES = Path("/opt")
BACKUPS = Path("/var/backups/yoodun-work-os")
UNIT = Path("/etc/systemd/system/yoodun-work-os.service")
DATABASE = Path("/var/lib/yoodun-work-os/work_os.sqlite3")
SERVICE = "yoodun-work-os.service"
LOCK = Path("/run/lock/yoodun-work-os-deploy.lock")
RELEASE_NAME = re.compile(r"release-([0-9a-f]{64})\.tar\.gz\Z")
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_MEMBER = 16 * 1024 * 1024
MAX_MEMBERS = 1000
ALLOWED_SUFFIXES = {
    ".py", ".js", ".css", ".html", ".json", ".svg", ".png",
    ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2",
    ".ttf", ".txt", ".typed",
}
REQUIRED = {"app/server.py", "app/static/index.html", "app/static/martial.js"}


class ReleaseError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, text=True, capture_output=True)


def service_active() -> bool:
    return run("systemctl", "is-active", "--quiet", SERVICE, check=False).returncode == 0


def archive_name(name: str, expected: str) -> None:
    require(RELEASE_NAME.fullmatch(name) is not None, "invalid archive name")
    require(name == f"release-{expected}.tar.gz", "archive name and SHA-256 differ")


def incoming_file(name: str) -> Path:
    user = pwd.getpwnam(DEPLOY_USER)
    folder = INCOMING.lstat()
    require(stat.S_ISDIR(folder.st_mode) and folder.st_uid == user.pw_uid,
            "incoming directory must belong to the deployment account")
    path = INCOMING / name
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == user.pw_uid,
            "archive must be a regular deployment-account file")
    require(0 < info.st_size <= MAX_ARCHIVE, "archive exceeds size limit")
    return path


def open_regular(path: Path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or not (0 < info.st_size <= MAX_ARCHIVE):
        os.close(fd)
        raise ReleaseError("archive is not a bounded regular file")
    return os.fdopen(fd, "rb")


def sha256_stream(source) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def valid_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    parts = path.parts
    return (
        len(parts) >= 2 and parts[0] == "app" and
        name == path.as_posix() and "\\" not in name and
        not path.is_absolute() and
        all(part not in {"", ".", "..", "__pycache__"} and
            not part.startswith(".") for part in parts) and
        path.suffix.lower() in ALLOWED_SUFFIXES and
        not name.endswith((".pyc", ".pyo")) and
        ".dist-info" not in name
    )


def validate_archive(path: Path, expected: str) -> list[str]:
    with open_regular(path) as source:
        require(sha256_stream(source) == expected, "archive SHA-256 mismatch")
        source.seek(0)
        try:
            with tarfile.open(fileobj=source, mode="r:gz") as bundle:
                members = bundle.getmembers()
                require(0 < len(members) <= MAX_MEMBERS, "invalid member count")
                names: set[str] = set()
                expanded = 0
                for member in members:
                    name = member.name
                    require(member.isfile() and valid_member_name(name),
                            f"unsafe archive member: {name!r}")
                    require(name not in names, f"duplicate archive member: {name!r}")
                    require(0 <= member.size <= MAX_MEMBER,
                            f"oversized archive member: {name!r}")
                    names.add(name)
                    expanded += member.size
                    require(expanded <= MAX_EXPANDED, "archive expands beyond limit")
                    # Reject a file whose parent is another archive file.
                    parts = PurePosixPath(name).parts
                    require(not any("/".join(parts[:n]) in names for n in range(2, len(parts))),
                            f"file/directory conflict: {name!r}")
                    if name.endswith(".py"):
                        content = bundle.extractfile(member)
                        require(content is not None, f"unreadable member: {name!r}")
                        compile(content.read(), name, "exec")
                require(REQUIRED <= names, "archive lacks a required application entry")
                for name in names:
                    parts = PurePosixPath(name).parts
                    require(not any("/".join(parts[:n]) in names for n in range(2, len(parts))),
                            f"file/directory conflict: {name!r}")
                return sorted(names)
        except (tarfile.TarError, OSError, SyntaxError) as exc:
            raise ReleaseError(f"invalid release archive: {exc}") from exc


def current_release() -> tuple[Path, str]:
    unit = UNIT.read_text(encoding="utf-8")
    starts = [line.partition("=")[2].strip() for line in unit.splitlines()
              if line.startswith("ExecStart=")]
    require(len(starts) == 1, "expected exactly one service ExecStart")
    match = re.fullmatch(
        r"/usr/bin/python3 (/opt/yoodun-work-os[-A-Za-z0-9_]+/app/server\.py)"
        r"( --host 127\.0\.0\.1 --port 18766)", starts[0]
    )
    require(match is not None, "service command differs from reviewed Work OS layout")
    root = Path(match.group(1)).parent.parent
    require(root.parent == RELEASES and root.is_dir() and not root.is_symlink(),
            "active release directory is invalid")
    require(f"WorkingDirectory={root}/app" in unit,
            "service working directory differs from active release")
    require(root.stat().st_uid == 0, "active release must be root-owned")
    require((root / "app/server.py").is_file(), "active server entry is missing")
    return root, unit


def preflight(name: str, expected: str) -> tuple[Path, Path, str, list[str]]:
    archive_name(name, expected)
    source = incoming_file(name)
    members = validate_archive(source, expected)
    root, unit = current_release()
    require(DATABASE.is_file(), "Work OS database is missing")
    require(service_active(), "Work OS service is not active before release")
    return source, root, unit, members


def copy_verified(source: Path, destination: Path, expected: str) -> None:
    with open_regular(source) as inp, destination.open("xb") as out:
        os.chmod(destination, 0o600)
        shutil.copyfileobj(inp, out, length=1024 * 1024)
    validate_archive(destination, expected)


def extract_app(archive: Path, destination: Path) -> None:
    app = destination / "app"
    require(app.is_dir() and not app.is_symlink(), "cloned application directory is invalid")
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            folder = target.parent
            while folder != app.parent:
                require(folder.is_dir() and not folder.is_symlink(),
                        f"unsafe application directory: {folder}")
                folder.chmod(0o755)
                folder = folder.parent
            require(not target.is_symlink() and
                    (not target.exists() or (target.is_file() and target.stat().st_nlink == 1)),
                    f"unsafe application file: {target}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
            with bundle.extractfile(member) as source, os.fdopen(os.open(target, flags, 0o644), "wb") as out:
                shutil.copyfileobj(source, out, length=1024 * 1024)
            target.chmod(0o644)


def backup_database(target: Path) -> None:
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as source:
        with sqlite3.connect(target) as backup:
            source.backup(backup)
            require(backup.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                    "database backup failed integrity check")
    target.chmod(0o600)


def restore_database(source_path: Path) -> None:
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(DATABASE) as target:
            source.backup(target)
            require(target.execute("PRAGMA quick_check").fetchone()[0] == "ok",
                    "database restore failed integrity check")


def replace_unit(contents: str) -> None:
    pending = UNIT.with_name(UNIT.name + f".deploy-{os.getpid()}")
    try:
        with pending.open("x", encoding="utf-8") as stream:
            stream.write(contents)
        pending.chmod(0o644)
        os.replace(pending, UNIT)
    finally:
        pending.unlink(missing_ok=True)


def health_check(app: Path) -> None:
    expected = hashlib.sha256((app / "static/martial.js").read_bytes()).hexdigest()
    for _ in range(10):
        if service_active():
            try:
                with urllib.request.urlopen("http://127.0.0.1:18766/", timeout=3) as response:
                    homepage = response.read(2_000_000)
                with urllib.request.urlopen("http://127.0.0.1:18766/martial.js", timeout=3) as response:
                    actual = hashlib.sha256(response.read(2_000_000)).hexdigest()
                if b"Work OS" in homepage and actual == expected:
                    return
            except OSError:
                pass
        time.sleep(1)
    raise ReleaseError("new service failed local HTTP or static-asset health check")


def deploy(name: str, expected: str) -> None:
    require(os.geteuid() == 0, "apply requires root via the dedicated sudo rule")
    with LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        source, current, unit_text, members = preflight(name, expected)
        marker = current / ".yoodun-release.sha256"
        if marker.is_file() and marker.read_text(encoding="ascii").strip() == expected:
            print(f"Already deployed: {current}")
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        new = RELEASES / f"yoodun-work-os-release-{stamp}-{expected[:12]}"
        require(not new.exists() and not new.is_symlink(), "release destination exists")
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = BACKUPS / f"ssh-release-{stamp}-{expected[:12]}-{os.getpid()}"
        backup.mkdir(mode=0o700)
        staged = backup / "release.tar.gz"
        copy_verified(source, staged, expected)
        (backup / "service.before").write_text(unit_text, encoding="utf-8")
        (backup / "service.before").chmod(0o600)
        switched = False
        started_new = False
        backup_ready = False
        try:
            shutil.copytree(current, new, symlinks=True)
            extract_app(staged, new)
            (new / ".yoodun-release.sha256").write_text(expected + "\n", encoding="ascii")
            os.chmod(new, 0o755)
            # The archive contains only app files. The service unit and registry
            # are retained from the reviewed active release.
            next_unit = unit_text.replace(str(current), str(new))
            require(next_unit != unit_text, "service unit did not reference active release")
            switched = True
            run("systemctl", "stop", SERVICE)
            backup_database(backup / "work_os.sqlite3")
            backup_ready = True
            replace_unit(next_unit)
            run("systemctl", "daemon-reload")
            started_new = True
            run("systemctl", "start", SERVICE)
            health_check(new / "app")
            print(f"Deployed: {new}")
            print(f"Backup: {backup}")
            print(f"Files: {len(members)}")
        except Exception as exc:
            if switched:
                errors = []
                for action in (
                    lambda: run("systemctl", "stop", SERVICE),
                    lambda: restore_database(backup / "work_os.sqlite3") if backup_ready and started_new else None,
                    lambda: replace_unit(unit_text),
                    lambda: run("systemctl", "daemon-reload"),
                    lambda: run("systemctl", "start", SERVICE),
                    lambda: health_check(current / "app"),
                ):
                    try:
                        action()
                    except Exception as rollback_error:
                        errors.append(str(rollback_error))
                if errors:
                    raise ReleaseError(
                        f"release failed: {exc}; rollback needs attention: {'; '.join(errors)}; backup: {backup}"
                    ) from exc
            shutil.rmtree(new, ignore_errors=True)
            raise ReleaseError(f"release failed; previous service restored: {exc}; backup: {backup}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("archive_name")
    parser.add_argument("sha256")
    args = parser.parse_args()
    try:
        require(re.fullmatch(r"[0-9a-f]{64}", args.sha256) is not None, "invalid SHA-256")
        require(os.geteuid() == 0, "run via the dedicated root-owned sudo entry")
        if args.check:
            _, current, _, members = preflight(args.archive_name, args.sha256)
            print(f"CHECK OK: {len(members)} app files; active release {current}")
        else:
            deploy(args.archive_name, args.sha256)
        return 0
    except (ReleaseError, FileNotFoundError, PermissionError, subprocess.CalledProcessError) as exc:
        print(f"Deploy error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

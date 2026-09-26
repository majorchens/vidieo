#!/usr/bin/env python3
"""Import the five approved NPC voice samples without changing master facts.

Run as the Work OS service user with YOODUN_DATA_DIR set. The source archive is
validated in full before any database or file write. WAV is a voice generation
reference; MP3 is an audition only. Neither is a teacher introduction or TTS.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import secrets
import sys
import tarfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import store


VERSION = "npc-voices-20260919-v1"
PROJECT = "wuxiang"
ACTOR = "u_system"
MAX_MEMBER = 10_000_000
MAX_ARCHIVE_CONTENT = 60_000_000
EXPECTED = {
    "pongda": ("Pongda", "活泼小淘气", "P12", "P12"),
    "boor": ("Boor", "柔暖小伙伴", "P11", "P11"),
    "wongkey": ("Wongkey", "清脆小机灵", "P07", "P07"),
    "bara": ("Bara", "老树沉声", "bara_01", "A01"),
    "cryn": ("Cryn", "柔低清韵", "cryn_04", "C04"),
}
MEDIA = (("voice_reference", "reference", ".wav"),
         ("voice_audition", "audition", ".mp3"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _voice_archive(path: Path) -> tuple[dict, dict[str, bytes]]:
    """Reject extra members, links, path traversal, changed mapping or media."""
    if not path.is_file():
        raise ValueError("voice archive missing")
    with tarfile.open(path, "r:gz") as archive:
        members = {}
        total = 0
        for member in archive:
            name = member.name
            if (not member.isfile() or name != Path(name).name or
                    name in {"", ".", ".."} or name in members or
                    member.size < 1 or member.size > MAX_MEMBER):
                raise ValueError("voice archive has an unsafe member")
            total += member.size
            if total > MAX_ARCHIVE_CONTENT:
                raise ValueError("voice archive exceeds content limit")
            members[name] = member
        manifest_member = members.get("voice_manifest.json")
        if not manifest_member or manifest_member.size > 100_000:
            raise ValueError("voice manifest missing or oversized")
        manifest = json.loads(archive.extractfile(manifest_member).read())
        if manifest.get("version") != VERSION or not isinstance(manifest.get("voices"), list):
            raise ValueError("unexpected voice source version")
        voices = manifest["voices"]
        if len(voices) != len(EXPECTED):
            raise ValueError("voice source must contain exactly five masters")
        content = {}
        expected_members = {"voice_manifest.json"}
        seen = set()
        for voice in voices:
            master = voice.get("character_id")
            if master not in EXPECTED or master in seen:
                raise ValueError("unknown or duplicate voice master")
            seen.add(master)
            name, voice_name, candidate, display = EXPECTED[master]
            if (voice.get("character_name"), voice.get("voice_name"),
                    voice.get("candidate_id"), voice.get("display_code")) != (
                        name, voice_name, candidate, display):
                raise ValueError(f"approved voice mapping differs for {master}")
            if voice.get("reference_language") != "English" or voice.get("sample_rate") != 24000:
                raise ValueError(f"reference language or rate differs for {master}")
            reference_text = voice.get("reference_text")
            if not isinstance(reference_text, str) or not reference_text.strip():
                raise ValueError(f"reference text missing for {master}")
            text_file = f"{master}_reference.txt"
            if voice.get("reference_text_file") != text_file:
                raise ValueError(f"reference text filename differs for {master}")
            expected_members.add(text_file)
            for role, key, suffix in MEDIA:
                item = voice.get(key)
                filename = f"{master}_{key}{suffix}"
                if not isinstance(item, dict) or item.get("file") != filename:
                    raise ValueError(f"{key} filename differs for {master}")
                if (not isinstance(item.get("sha256"), str) or
                        not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) or
                        not isinstance(item.get("bytes"), int) or
                        item["bytes"] < 1 or item["bytes"] > MAX_MEMBER):
                    raise ValueError(f"{key} metadata invalid for {master}")
                expected_members.add(filename)
        if set(members) != expected_members:
            raise ValueError("voice archive member list differs from manifest")
        for filename in expected_members - {"voice_manifest.json"}:
            item = archive.extractfile(members[filename])
            if item is None:
                raise ValueError("voice archive member unreadable")
            content[filename] = item.read()
        for voice in voices:
            master = voice["character_id"]
            if content[f"{master}_reference.txt"].decode("utf-8-sig").strip() != voice["reference_text"].strip():
                raise ValueError(f"reference text mismatch for {master}")
            for _, key, suffix in MEDIA:
                item = voice[key]
                data = content[f"{master}_{key}{suffix}"]
                if len(data) != item["bytes"] or _sha(data) != item["sha256"]:
                    raise ValueError(f"{key} size or SHA mismatch for {master}")
                if key == "reference":
                    try:
                        with wave.open(io.BytesIO(data), "rb") as wav:
                            seconds = wav.getnframes() / wav.getframerate()
                            if (wav.getnchannels() != 1 or wav.getframerate() != 24000 or
                                    wav.getsampwidth() != 3 or
                                    abs(seconds - float(voice["seconds"])) > .05):
                                raise ValueError("WAV format or duration differs")
                    except (EOFError, wave.Error, ZeroDivisionError) as exc:
                        raise ValueError(f"invalid WAV for {master}") from exc
                elif not (data[:3] == b"ID3" or
                          len(data) >= 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0):
                    raise ValueError(f"invalid MP3 for {master}")
        return manifest, content


def _controlled_asset(row, sha: str, suffix: str) -> bool:
    if (not row or row["project_id"] != PROJECT or row["type"] != "audio" or
            row["status"] != "active" or row["sha256"] != sha):
        return False
    root = (store.DATA / "uploads" / PROJECT).resolve()
    raw = Path(row["storage_ref"])
    try:
        path = raw.resolve(strict=True)
        return (path.is_file() and path.is_relative_to(root) and
                path.suffix.lower() == suffix and store.digest_file(path) == sha)
    except (OSError, RuntimeError):
        return False


def _active_link(c, master: str, role: str):
    return c.execute("""SELECT l.asset_id,a.project_id,a.type,a.status,a.sha256,a.storage_ref
                      FROM martial_mm_asset_links l LEFT JOIN assets a ON a.id=l.asset_id
                      WHERE l.scope='master' AND l.scope_id=? AND l.role=? AND l.status='active'""",
                     (master, role)).fetchone()


def _reusable(c, sha: str, suffix: str):
    for row in c.execute("""SELECT id,project_id,type,status,sha256,storage_ref FROM assets
                            WHERE project_id=? AND type='audio' AND status='active' AND sha256=?
                            ORDER BY created_at,id""", (PROJECT, sha)):
        if _controlled_asset(row, sha, suffix):
            return row["id"]
    return None


def _check_database(c, manifest: dict) -> list[dict]:
    tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"users", "projects", "assets", "martial_masters", "martial_master_versions",
                "martial_mm_asset_links", "martial_mm_voice_personas"}
    if not required <= tables:
        raise ValueError("Work OS martial asset schema is not initialized")
    if not c.execute("SELECT 1 FROM users WHERE id=?", (ACTOR,)).fetchone():
        raise ValueError("system actor is missing")
    if not c.execute("SELECT 1 FROM projects WHERE id=?", (PROJECT,)).fetchone():
        raise ValueError("wuxiang project is missing")
    plan = []
    for voice in manifest["voices"]:
        master = voice["character_id"]
        row = c.execute("SELECT name,current_version FROM martial_masters WHERE id=?", (master,)).fetchone()
        if not row or row["name"] != EXPECTED[master][0]:
            raise ValueError(f"master record differs for {master}")
        current = c.execute("""SELECT payload FROM martial_master_versions
                               WHERE master_id=? AND version=?""", (master, row["current_version"])).fetchone()
        payload = store.parse(current["payload"], {}) if current else {}
        if not isinstance(payload, dict) or payload.get("voice_id") != EXPECTED[master][3]:
            raise ValueError(f"current master voice code differs for {master}")
        persona = c.execute("""SELECT payload FROM martial_mm_voice_personas
                               WHERE master_id=? AND status='active'""", (master,)).fetchone()
        persona_payload = store.parse(persona["payload"], {}) if persona else {}
        if persona and (not isinstance(persona_payload, dict) or
                        persona_payload.get("voice_id") not in {None, "", EXPECTED[master][3]}):
            raise ValueError(f"active voice persona conflicts for {master}")
        for role, key, suffix in MEDIA:
            sha = voice[key]["sha256"]
            active = _active_link(c, master, role)
            if active and not _controlled_asset(active, sha, suffix):
                raise ValueError(f"existing {role} conflicts for {master}")
            plan.append({"master": master, "voice": voice, "role": role, "key": key,
                         "suffix": suffix, "sha": sha, "active": bool(active),
                         "reuse": _reusable(c, sha, suffix) if not active else None})
    return plan


def _create_asset(c, item: dict, data: bytes, created_files: list[Path]) -> str:
    voice = item["voice"]
    role, sha, suffix = item["role"], item["sha"], item["suffix"]
    directory = store.DATA / "uploads" / PROJECT
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"fixed_voice_{sha}{suffix}"
    if dest.exists() or dest.is_symlink():
        if dest.is_symlink() or not dest.is_file() or store.digest_file(dest) != sha:
            raise ValueError("controlled voice destination conflicts with existing file")
    else:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created_files.append(dest)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    asset_id = "a_" + secrets.token_hex(8)
    filename = f"{voice['character_name']} 定稿声线" + ("生成参考.wav" if role == "voice_reference" else "试听.mp3")
    version = c.execute("""SELECT COALESCE(MAX(version),0)+1 FROM assets
                           WHERE project_id=? AND name=?""", (PROJECT, filename)).fetchone()[0]
    source = [{"source": VERSION, "master_id": item["master"], "role": role,
               "voice_name": voice["voice_name"], "candidate_id": voice["candidate_id"],
               "display_code": voice["display_code"], "reference_language": "English",
               "reference_text": voice["reference_text"], "original_file": voice[item["key"]]["file"]}]
    c.execute("""INSERT INTO assets(id,project_id,type,name,storage_ref,sha256,version,
               source_refs,status,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (asset_id, PROJECT, "audio", filename, str(dest), sha, version,
               store.dumps(source), "active", store.now(), ACTOR))
    store.audit(c, ACTOR, "asset.fixed_voice_import", None,
                {"asset_id": asset_id, "master_id": item["master"], "role": role, "version": VERSION})
    return asset_id


def import_voices(archive_path: Path, check_only: bool = False) -> dict:
    manifest, content = _voice_archive(Path(archive_path))
    created_files = []
    try:
        with store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            plan = _check_database(c, manifest)
            summary = {master: {"reference": "", "audition": ""} for master in EXPECTED}
            counts = {"created_assets": 0, "reused_assets": 0, "new_links": 0,
                      "existing_links": 0}
            for item in plan:
                key = item["key"]
                if item["active"]:
                    summary[item["master"]][key] = "already_linked"
                    counts["existing_links"] += 1
                    continue
                asset_id = item["reuse"]
                if asset_id:
                    summary[item["master"]][key] = "reused_asset"
                    counts["reused_assets"] += 1
                else:
                    summary[item["master"]][key] = "new_asset"
                    counts["created_assets"] += 1
                    if not check_only:
                        data = content[item["voice"][key]["file"]]
                        asset_id = _create_asset(c, item, data, created_files)
                counts["new_links"] += 1
                if not check_only:
                    version = c.execute("""SELECT COALESCE(MAX(version),0)+1 FROM martial_mm_asset_links
                                           WHERE scope='master' AND scope_id=? AND role=?""",
                                        (item["master"], item["role"])).fetchone()[0]
                    c.execute("""INSERT INTO martial_mm_asset_links(id,scope,scope_id,role,version,
                               asset_id,status,source_ref,created_by,created_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?)""",
                              ("mma_" + secrets.token_hex(8), "master", item["master"],
                               item["role"], version, asset_id, "active",
                               f"{VERSION}:{item['voice'][key]['file']}:sha256:{item['sha']}",
                               ACTOR, store.now()))
                    store.audit(c, ACTOR, "martial.fixed_voice_link", None,
                                {"master_id": item["master"], "role": item["role"],
                                 "asset_id": asset_id, "source": VERSION})
            if check_only:
                c.rollback()
            return {"source_version": VERSION, "check_only": check_only,
                    "counts": counts, "masters": summary}
    except Exception:
        for path in created_files:
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="approved five-voice tar.gz")
    parser.add_argument("--check-only", action="store_true", help="validate all inputs and active links without writing")
    args = parser.parse_args()
    result = import_voices(args.archive, args.check_only)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

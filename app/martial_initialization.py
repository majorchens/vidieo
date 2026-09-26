"""Confirm unchanged martial facts imported from the verified employee DOCX.

Call ``initialize_confirmed_import`` after ``store.initialize`` and
``martial.initialize``. This is a one-time, repeatable source confirmation,
not an approval of later employee edits. It never creates martial facts or
changes an existing active version, newer draft, motion, package, or candidate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import store


SOURCE_SHA256 = "ef38507ceb692da5c03ae973ea52502e1b68f9031999b966d0a3ea577cd09c21"
REGISTRY_SHA256 = "85a26c23d1641dbb30bc3f762ff0fd457920a7f4fc9f30498f06b950c92d3bc3"
REGISTRY = Path(__file__).resolve().parents[1] / "registry" / "martial_dictionary.json"
MOVE_FIELDS = ("chinese_name", "english_name", "chinese_action", "english_action",
               "chinese_coaching", "english_coaching", "breathing_notes", "safety_notes")
REQUIRED_SOURCE_FIELDS = ("chinese_action", "english_action", "chinese_coaching", "english_coaching")


def _verified_dictionary(path: Path) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != REGISTRY_SHA256:
        raise ValueError("武学词典与已核查的原始 DOCX 提取结果不一致")
    data = json.loads(raw)
    if data.get("source_sha256") != SOURCE_SHA256:
        raise ValueError("武学词典的 DOCX 来源哈希不一致")
    if len(data.get("arts", [])) != 5 or sum(len(a["moves"]) for a in data["arts"]) != 47:
        raise ValueError("武学词典不是已核查的 5 套武学和 47 条招式")
    return data


def _art_expected(source: dict, art: dict, art_names: dict) -> dict:
    chinese_name, english_name, master_id = art_names[art["id"]]
    intro = art["intro"]
    traits = intro.partition("：")[2].partition("——")[0].split("，") if "：" in intro else []
    return {
        "chinese_name": chinese_name,
        "english_name": english_name,
        "volume": "第一卷",
        "category": "入门" if art["id"] == "beginner" else "流派套路",
        "description": intro,
        "style_traits": [part.strip() for part in traits if part.strip()],
        "master_id": master_id,
        "planned_moves": art["planned_moves"],
        "source_ref": source["source_url"] + ";docx-sha256:" + SOURCE_SHA256,
    }


def _art_state(connection, source: dict, art: dict, art_names: dict) -> tuple[str, str | None, dict | None]:
    expected = _art_expected(source, art, art_names)
    row = connection.execute("SELECT * FROM martial_arts WHERE id=?", (art["id"],)).fetchone()
    version = connection.execute("SELECT * FROM martial_art_versions WHERE art_id=? AND version=1", (art["id"],)).fetchone()
    if row is None or version is None:
        return "skip", "missing_import", None
    row, version = dict(row), dict(version)
    if connection.execute("SELECT COUNT(*) FROM martial_art_versions WHERE art_id=?", (art["id"],)).fetchone()[0] != 1:
        return "skip", "later_version_exists", None
    original = {field: row[field] for field in expected if field != "style_traits"}
    original["style_traits"] = store.parse(row["style_traits"], [])
    version_payload = store.parse(version["payload"], {})
    payload_expected = {field: value for field, value in expected.items() if field != "source_ref"}
    if original != expected or version_payload != payload_expected or version["source_ref"] != expected["source_ref"]:
        return "skip", "source_mismatch", None
    if version["created_by"] != "u_system":
        return "skip", "human_authored_version", None
    if (row["status"], row["version"], row["draft_version"], version["status"]) == ("needs_review", 1, 1, "needs_review"):
        return "activate", None, version
    if (row["status"], row["version"], row["draft_version"], version["status"]) == ("active", 1, 0, "active"):
        return "preserve", None, version
    return "skip", "non_import_state", None


def _move_state(connection, art_id: str, source_move: dict) -> tuple[str, str | None, dict | None]:
    ordinal = source_move["order"]
    move_id = f"mv_{art_id}_{ordinal:02d}"
    row = connection.execute("SELECT * FROM martial_moves WHERE id=?", (move_id,)).fetchone()
    version = connection.execute("SELECT * FROM martial_move_versions WHERE move_id=? AND version=1", (move_id,)).fetchone()
    if row is None or version is None:
        return "skip", "missing_import", None
    row, version = dict(row), dict(version)
    if connection.execute("SELECT COUNT(*) FROM martial_move_versions WHERE move_id=?", (move_id,)).fetchone()[0] != 1:
        return "skip", "later_version_exists", None
    expected = {field: source_move.get(field, "") for field in MOVE_FIELDS}
    if (row["martial_art_id"] != art_id or row["ordinal"] != ordinal or
            store.parse(version["payload"], {}) != expected or version["source_ref"] != source_move["source_ref"]):
        return "skip", "source_mismatch", None
    if version["created_by"] != "u_system":
        return "skip", "human_authored_version", None
    if (row["current_version"], row["draft_version"], version["status"]) == (0, 1, "needs_review"):
        if version["submitted_by"] or version["rejected_by"] or store.parse(version["review_flags"], []):
            return "skip", "employee_review_in_progress", None
        return "activate", None, version
    if (row["current_version"], row["draft_version"], version["status"]) == (1, 0, "active"):
        return "preserve", None, version
    return "skip", "non_import_state", None


def _record(connection, kind: str, record_id: str, version: dict, source_ref: str,
            status: str, missing_fields: list[str]) -> bool:
    existing = connection.execute(
        "SELECT source_sha256,registry_sha256,source_ref,status,missing_fields FROM martial_import_confirmations "
        "WHERE kind=? AND record_id=? AND version=1", (kind, record_id)).fetchone()
    values = (SOURCE_SHA256, REGISTRY_SHA256, source_ref, status, store.dumps(missing_fields))
    if existing:
        if tuple(existing) != values:
            raise ValueError(f"现有来源确认记录冲突：{kind}/{record_id}")
        return False
    connection.execute(
        "INSERT INTO martial_import_confirmations(kind,record_id,version,status,source_sha256,registry_sha256,"
        "source_ref,missing_fields,imported_at,confirmed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (kind, record_id, 1, status, SOURCE_SHA256, REGISTRY_SHA256, source_ref,
         store.dumps(missing_fields), version["created_at"], store.now()))
    return True


def initialize_confirmed_import(dictionary_path: Path = REGISTRY) -> dict:
    """Activate only untouched V1 source facts and return a migration report.

    Existing active V1 facts are preserved and receive provenance metadata.
    Rows with source differences or any later version are left untouched.
    Repeated calls write no new records or audit event.
    """
    source = _verified_dictionary(Path(dictionary_path))
    # The art label/master mapping is the same fixed mapping used by the
    # original importer. The DOCX itself provides the intro and 47 move rows.
    from martial import ARTS
    art_names = {art_id: (chinese, english, master) for art_id, chinese, english, master in ARTS}
    if set(art_names) != {art["id"] for art in source["arts"]}:
        raise ValueError("武学词典与导入器的五套武学不一致")
    result = {
        "source_sha256": SOURCE_SHA256, "registry_sha256": REGISTRY_SHA256,
        "activated_arts": [], "activated_moves": [], "preserved_arts": [], "preserved_moves": [],
        "skipped_arts": {}, "skipped_moves": {}, "provenance_created": 0,
        "normal_production_approval_count": 0,
    }
    with store.connect() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS martial_import_confirmations(
            kind TEXT NOT NULL CHECK(kind IN ('art','move')),
            record_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('confirmed','confirmed_with_missing_fields')),
            source_sha256 TEXT NOT NULL,
            registry_sha256 TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            missing_fields TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            PRIMARY KEY(kind,record_id,version))""")
        for art in source["arts"]:
            art_id = art["id"]
            state, reason, version = _art_state(connection, source, art, art_names)
            if state == "skip":
                result["skipped_arts"][art_id] = reason
            else:
                if state == "activate":
                    connection.execute("UPDATE martial_art_versions SET status='active' WHERE art_id=? AND version=1", (art_id,))
                    connection.execute("UPDATE martial_arts SET status='active',draft_version=0,updated_at=? WHERE id=?", (store.now(), art_id))
                result[f"{state}d_arts"].append(art_id)
                if _record(connection, "art", art_id, version, version["source_ref"], "confirmed", []):
                    result["provenance_created"] += 1
            for move in art["moves"]:
                move_id = f"mv_{art_id}_{move['order']:02d}"
                state, reason, version = _move_state(connection, art_id, move)
                if state == "skip":
                    result["skipped_moves"][move_id] = reason
                    continue
                missing = [field for field in REQUIRED_SOURCE_FIELDS if not move.get(field)]
                confirmation = "confirmed_with_missing_fields" if missing else "confirmed"
                if state == "activate":
                    connection.execute("UPDATE martial_move_versions SET status='active' WHERE move_id=? AND version=1", (move_id,))
                    connection.execute("UPDATE martial_moves SET current_version=1,draft_version=0,updated_at=? WHERE id=?", (store.now(), move_id))
                result[f"{state}d_moves"].append(move_id)
                if _record(connection, "move", move_id, version, version["source_ref"], confirmation, missing):
                    result["provenance_created"] += 1
        if result["activated_arts"] or result["activated_moves"] or result["provenance_created"]:
            store.audit(connection, "u_system", "martial.import.source_confirm", None, {
                "source_sha256": SOURCE_SHA256,
                "activated_arts": len(result["activated_arts"]),
                "activated_moves": len(result["activated_moves"]),
                "provenance_created": result["provenance_created"],
                "skipped_arts": result["skipped_arts"], "skipped_moves": result["skipped_moves"],
            })
    return result


def initialization_stats() -> dict:
    """Small read-only summary for an admin status endpoint or release check."""
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT kind,status,COUNT(*) AS n FROM martial_import_confirmations "
            "WHERE source_sha256=? GROUP BY kind,status", (SOURCE_SHA256,)).fetchall()
        incomplete = [row[0] for row in connection.execute(
            "SELECT record_id FROM martial_import_confirmations WHERE kind='move' "
            "AND status='confirmed_with_missing_fields' AND source_sha256=? ORDER BY record_id", (SOURCE_SHA256,))]
    counts = {"art": {"confirmed": 0, "confirmed_with_missing_fields": 0},
              "move": {"confirmed": 0, "confirmed_with_missing_fields": 0}}
    for row in rows:
        counts[row["kind"]][row["status"]] = row["n"]
    return {"source_sha256": SOURCE_SHA256, "registry_sha256": REGISTRY_SHA256,
            "arts": counts["art"], "moves": counts["move"],
            "incomplete_move_ids": incomplete, "normal_production_approval_count": 0}

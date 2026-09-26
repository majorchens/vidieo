#!/usr/bin/env python3
"""Export an approved lesson manifest for Git review without copying media."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--lesson-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    database = args.data_dir / "work_os.sqlite3"
    if not database.is_file():
        parser.error("Work OS database not found")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT current_version,approved_manifest,approved_sha256 FROM martial_lesson_packages WHERE id=?",
                                 (args.lesson_id,)).fetchone()
    if not row or not row[1] or hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
        parser.error("lesson has no intact approved manifest")
    manifest = json.loads(row[1])
    if manifest.get("lesson_id") != args.lesson_id or manifest.get("schema") != "MartialArtsLessonPackage/v1":
        parser.error("lesson manifest identity or schema is invalid")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"version": row[0], "sha256": row[2], "package": manifest},
                                   ensure_ascii=False, indent=2) + "\n")
    print(f"{args.lesson_id} v{row[0]} {row[2]}")


if __name__ == "__main__":
    main()

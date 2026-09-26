"""Focused, isolated checks for audited martial image initialization."""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def tiny_png(index: int) -> bytes:
    """A complete 1×1 PNG; color is stable and unique for each fixture."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload +
                struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

    pixel = bytes(((index >> 16) & 255, (index >> 8) & 255, index & 255))
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\x00" + pixel)) +
            chunk(b"IEND", b""))


class MartialAssetImportTest(unittest.TestCase):
    def setUp(self):
        import martial
        import martial_assets
        import store

        self.martial, self.assets, self.store = martial, martial_assets, store
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.originals = (store.DATA, store.DB, dict(store.PROJECT_ROOTS),
                          store.SHARED_ROOT, martial_assets.MANIFEST)
        store.DATA = self.root / "data"
        store.DB = store.DATA / "work_os.sqlite3"
        store.PROJECT_ROOTS["wuxiang"] = self.root / "project"
        store.PROJECT_ROOTS["wuxiang"].mkdir(parents=True)
        store.SHARED_ROOT = self.root / "shared"
        store.SHARED_ROOT.mkdir()
        store.initialize()
        martial.initialize()
        founder_id = store.create_user("asset-founder", "素材负责人", "founder", "test-only-password")
        specialist_id = store.create_user("asset-specialist", "武学员工", "employee", "test-only-password")
        other_id = store.create_user("asset-other", "其他员工", "employee", "test-only-password")
        self.founder = {"id": founder_id, "role": "founder"}
        self.specialist = {"id": specialist_id, "role": "employee"}
        self.other = {"id": other_id, "role": "employee"}
        with store.connect() as c:
            c.execute("INSERT INTO martial_specialists(user_id,project_id,title,created_at) VALUES(?,?,?,?)",
                      (specialist_id, "wuxiang", "武术数字资产内容设计", store.now()))

        canonical = json.loads((ROOT / "registry" / "martial_asset_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(canonical["images"]), 59)
        self.content: dict[str, bytes] = {}
        contents_by_original_hash: dict[str, bytes] = {}
        self.entries: list[dict] = []
        for source in canonical["images"]:
            original_hash = source["sha256"]
            if original_hash not in contents_by_original_hash:
                contents_by_original_hash[original_hash] = tiny_png(len(contents_by_original_hash) + 1)
            content = contents_by_original_hash[original_hash]
            entry = dict(source)
            entry.update(size_bytes=len(content), width=1, height=1,
                         sha256=hashlib.sha256(content).hexdigest())
            self.entries.append(entry)
            self.content[entry["relative_path"]] = content
        self.manifest = self.root / "manifest.json"
        self._write_manifest()
        martial_assets.MANIFEST = self.manifest

    def tearDown(self):
        data, db, project_roots, shared_root, manifest = self.originals
        self.store.DATA, self.store.DB = data, db
        self.store.PROJECT_ROOTS.clear()
        self.store.PROJECT_ROOTS.update(project_roots)
        self.store.SHARED_ROOT = shared_root
        self.assets.MANIFEST = manifest
        self.temp.cleanup()

    def _write_manifest(self):
        self.manifest.write_text(json.dumps({"schema_version": 1,
                                             "source_root": str(self.root / "source"),
                                             "images": self.entries}, ensure_ascii=False),
                                 encoding="utf-8")

    def _entry(self, master_id: str, role: str) -> dict:
        return next(entry for entry in self.entries
                    if entry["master_id"] == master_id and entry["role"] == role)

    def _import(self, entry: dict, *, content: bytes | None = None, user: dict | None = None) -> dict:
        path = entry["relative_path"]
        body = self.content[path] if content is None else content
        return self.assets.import_asset(user or self.founder,
                                        {"relative_path": path,
                                         "upload": {"base64": base64.b64encode(body).decode()}})

    def _version(self, master_id: str, version: int) -> dict:
        with self.store.connect() as c:
            row = c.execute("SELECT payload FROM martial_master_versions WHERE master_id=? AND version=?",
                            (master_id, version)).fetchone()
        return self.store.parse(row["payload"], {})

    def test_manifest_and_hash_are_checked_before_an_import(self):
        entry = self._entry("pongda", "full_body_hero")
        with self.assertRaisesRegex(ValueError, "清单"):
            self.assets.import_asset(self.founder,
                                     {"relative_path": "unlisted/portrait.png", "upload": {"base64": "AA=="}})
        tampered = bytearray(self.content[entry["relative_path"]])
        tampered[-5] ^= 1
        with self.assertRaisesRegex(ValueError, "内容"):
            self._import(entry, content=bytes(tampered))
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_master_asset_imports").fetchone()[0], 0)
        imported = self._import(entry)
        self.assertTrue(imported["attached"])
        self.assertEqual(self._version("pongda", 2)["portrait"], imported["asset_id"])
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT current_version,draft_version FROM martial_masters WHERE id='pongda'").fetchone()[:], (2, 0))

    def test_all_59_import_once_and_retry_preserves_locked_versions(self):
        production = self.store.register_submission_asset("wuxiang", "cryn-live.png", tiny_png(1000), self.founder["id"])
        with self.store.connect() as c:
            v2 = self.store.parse(c.execute("SELECT payload FROM martial_master_versions WHERE master_id='cryn' AND version=2").fetchone()[0], {})
            v3 = dict(v2, portrait=production["id"])
            c.execute("INSERT INTO martial_master_versions(master_id,version,payload,status,source_ref,created_by,approved_by,created_at,approved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      ("cryn", 3, self.store.dumps(v3), "locked", "test-production-visual",
                       self.founder["id"], self.founder["id"], self.store.now(), self.store.now()))
            c.execute("UPDATE martial_masters SET current_version=3 WHERE id='cryn'")

        first = {entry["relative_path"]: self._import(entry) for entry in self.entries}
        second = {entry["relative_path"]: self._import(entry) for entry in self.entries}
        self.assertTrue(all(item["reused"] for item in second.values()))
        self.assertEqual({path: item["asset_id"] for path, item in first.items()},
                         {path: item["asset_id"] for path, item in second.items()})
        with self.store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_master_asset_imports").fetchone()[0], 59)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0],
                             len({entry["sha256"] for entry in self.entries}) + 1)
            versions = {row["id"]: (row["current_version"], row["draft_version"])
                        for row in c.execute("SELECT id,current_version,draft_version FROM martial_masters")}
            cryn_versions = {row[0] for row in c.execute("SELECT DISTINCT master_version FROM martial_master_asset_imports WHERE master_id='cryn'")}
        self.assertEqual(versions["cryn"], (3, 0))
        self.assertTrue(all(value == (2, 0) for key, value in versions.items() if key != "cryn"))
        self.assertEqual(cryn_versions, {2})
        self.assertEqual(self._version("cryn", 3)["portrait"], production["id"])
        self.assertEqual(self._version("cryn", 2)["portrait"],
                         first[self._entry("cryn", "full_body_hero")["relative_path"]]["asset_id"])
        for role, field in (("full_body_hero", "portrait"), ("front_view", "front_view"),
                            ("side_view", "side_view"), ("back_view", "back_view")):
            self.assertEqual(self._version("pongda", 2)[field],
                             first[self._entry("pongda", role)["relative_path"]]["asset_id"])

    def test_unmapped_page_limits_visibility_and_assignment(self):
        first, second = self.entries[0], self.entries[1]
        first["master_id"] = None
        second["master_id"] = None
        self._write_manifest()
        with self.assertRaises(PermissionError):
            self._import(first, user=self.specialist)
        a, b = self._import(first), self._import(second)
        with self.assertRaises(PermissionError):
            self.assets.unmapped(self.other)
        with self.assertRaises(PermissionError):
            self.assets.assign_unmapped(self.other, a["id"], "boor")
        self.assertEqual(self.assets.unmapped(self.specialist)["count"], 2)
        self.assertEqual(self.assets.assign_unmapped(self.specialist, a["id"], "boor")["master_id"], "boor")
        self.assertEqual(self.assets.assign_unmapped(self.specialist, b["id"], None, ignore=True), {"ignored": True})
        self.assertEqual(self.assets.unmapped(self.founder)["count"], 0)
        with self.store.connect() as c:
            row = c.execute("SELECT master_id,master_version FROM martial_master_asset_imports WHERE id=?", (a["id"],)).fetchone()
        self.assertEqual(tuple(row), ("boor", 2))


if __name__ == "__main__":
    unittest.main()

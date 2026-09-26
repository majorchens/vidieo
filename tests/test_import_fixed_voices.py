"""The approved voice import is atomic, source checked and repeatable."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "deploy"))

import import_fixed_voices as importer
import martial
import martial_multimodal_assets as mm
import store


def fixture_archive(path: Path, *, corrupt_sha=False, unsafe=False) -> tuple[dict, dict[str, bytes]]:
    files = {}
    voices = []
    for master, (name, voice_name, candidate, display) in importer.EXPECTED.items():
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(3)
            wav.setframerate(24000)
            wav.writeframes(hashlib.sha256(master.encode()).digest()[:3] * 240)
        reference = audio.getvalue()
        audition = b"ID3\x04\x00\x00\x00\x00\x00\x00" + master.encode()
        text = "Approved English source line"
        files[f"{master}_reference.wav"] = reference
        files[f"{master}_audition.mp3"] = audition
        files[f"{master}_reference.txt"] = (text + "\n").encode()
        voices.append({"character_id": master, "character_name": name,
                       "voice_name": voice_name, "candidate_id": candidate,
                       "display_code": display, "reference_language": "English",
                       "reference_text": text, "sample_rate": 24000, "seconds": .01,
                       "reference": {"file": f"{master}_reference.wav",
                                     "sha256": hashlib.sha256(reference).hexdigest(),
                                     "bytes": len(reference)},
                       "audition": {"file": f"{master}_audition.mp3",
                                    "sha256": hashlib.sha256(audition).hexdigest(),
                                    "bytes": len(audition)},
                       "reference_text_file": f"{master}_reference.txt"})
    manifest = {"version": importer.VERSION, "voices": voices}
    if corrupt_sha:
        manifest["voices"][0]["audition"]["sha256"] = "0" * 64
    files["voice_manifest.json"] = json.dumps(manifest).encode()
    if unsafe:
        files["../outside.txt"] = b"unsafe"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return manifest, files


class ApprovedVoiceImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_data, self.old_db = store.DATA, store.DB
        store.DATA = self.root / "data"
        store.DB = store.DATA / "work_os.sqlite3"
        store.initialize()
        martial.initialize()
        mm.initialize()
        self.archive = self.root / "approved.tar.gz"
        self.manifest, self.files = fixture_archive(self.archive)

    def tearDown(self):
        store.DATA, store.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def _master_snapshot(self):
        with store.connect() as c:
            return [tuple(row) for row in c.execute(
                "SELECT master_id,version,payload,status FROM martial_master_versions ORDER BY master_id,version")]

    def test_import_preview_and_repeat_preserve_masters_and_intro(self):
        # An existing controlled audio asset with the same SHA is reused.
        existing = store.register_submission_asset(
            "wuxiang", "approved-reference.wav", self.files["pongda_reference.wav"],
            importer.ACTOR)["id"]
        before = self._master_snapshot()
        dry = importer.import_voices(self.archive, check_only=True)
        self.assertEqual(dry["counts"]["new_links"], 10)
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_asset_links").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
        result = importer.import_voices(self.archive)
        self.assertEqual(result["counts"], {"created_assets": 9, "reused_assets": 1,
                                           "new_links": 10, "existing_links": 0})
        self.assertEqual(self._master_snapshot(), before)
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_voice_personas").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 10)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_asset_links WHERE role='intro_audio'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_asset_links WHERE status='active'").fetchone()[0], 10)
            row = c.execute("""SELECT asset_id FROM martial_mm_asset_links WHERE scope='master'
                              AND scope_id='pongda' AND role='voice_reference'""").fetchone()
            self.assertEqual(row["asset_id"], existing)
        view = mm.master_assets({"id": importer.ACTOR, "role": "manager"}, "pongda")
        self.assertTrue(view["voice_audition"]["available"])
        self.assertTrue(view["voice_reference"]["available"])
        self.assertIsNone(view["intro_audio"])
        again = importer.import_voices(self.archive)
        self.assertEqual(again["counts"], {"created_assets": 0, "reused_assets": 0,
                                          "new_links": 0, "existing_links": 10})
        self.assertEqual(self._master_snapshot(), before)

    def test_existing_other_voice_link_stops_before_changes(self):
        unrelated = store.register_submission_asset(
            "wuxiang", "other.wav", self.files["boor_reference.wav"], importer.ACTOR)["id"]
        with store.connect() as c:
            c.execute("""INSERT INTO martial_mm_asset_links(id,scope,scope_id,role,version,
                       asset_id,status,source_ref,created_by,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                      ("mma_existing", "master", "pongda", "voice_reference", 1,
                       unrelated, "active", "different approved sample", importer.ACTOR, store.now()))
        before = self._master_snapshot()
        with self.assertRaisesRegex(ValueError, "conflicts for pongda"):
            importer.import_voices(self.archive)
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_asset_links").fetchone()[0], 1)
        self.assertEqual(self._master_snapshot(), before)

    def test_reject_tampered_or_unsafe_archive_before_database_write(self):
        before = self._master_snapshot()
        fixture_archive(self.archive, corrupt_sha=True)
        with self.assertRaisesRegex(ValueError, "SHA mismatch"):
            importer.import_voices(self.archive)
        fixture_archive(self.archive, unsafe=True)
        with self.assertRaisesRegex(ValueError, "unsafe member"):
            importer.import_voices(self.archive)
        with store.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM martial_mm_asset_links").fetchone()[0], 0)
        self.assertEqual(self._master_snapshot(), before)


if __name__ == "__main__":
    unittest.main()

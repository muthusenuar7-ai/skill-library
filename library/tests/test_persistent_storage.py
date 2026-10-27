"""Import staging must survive separate requests without a shared local directory."""
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import InMemoryStorage
from django.test import SimpleTestCase, override_settings

from library import importer


@override_settings(PRIVATE_MEDIA_ROOT="Z:/not-a-local-staging-directory")
class SharedImportStorageTests(SimpleTestCase):
    def setUp(self):
        self.storage = InMemoryStorage()
        factory = patch.object(importer, "get_private_storage", return_value=self.storage)
        factory.start()
        self.addCleanup(factory.stop)
        self.owner = SimpleNamespace(pk=7)
        self.candidates = [{"key": "0", "title": "Shared upload", "attachments": [
            {"path": "references/guide.txt", "data": b"persistent supporting file\x00", "size": 27},
        ]}]
        self.report = SimpleNamespace(rejected=[], ignored=[], fatal=[])

    def test_manifest_and_attachment_survive_separate_storage_accesses(self):
        token = importer.stage_import(self.owner, self.candidates, self.report)
        manifest = importer.load_stage(token, self.owner)
        self.assertEqual(manifest["candidates"][0]["title"], "Shared upload")
        attachment = manifest["candidates"][0]["attachments"][0]
        self.assertEqual(importer.read_stage_blob(token, attachment), b"persistent supporting file\x00")
        self.assertIsNone(importer.load_stage(token, SimpleNamespace(pk=8)))
        importer.discard_stage(token)
        self.assertIsNone(importer.load_stage(token, self.owner))
        self.assertFalse(self.storage.exists(f"imports/{token}/att/{attachment['blob']}"))

    def test_expired_stages_are_unavailable_and_objects_are_cleaned_up(self):
        token = importer.stage_import(self.owner, self.candidates, self.report)
        manifest = importer.load_stage(token, self.owner)
        manifest["created_at"] = time.time() - 25 * 3600
        path = f"imports/{token}/manifest.json"
        self.storage.delete(path)
        self.storage.save(path, ContentFile(json.dumps(manifest).encode()))
        self.assertIsNone(importer.load_stage(token, self.owner))
        self.assertFalse(self.storage.exists(path))

    def test_staged_blob_paths_cannot_escape_the_session(self):
        for token, blob in (("../other", "1.bin"), ("a" * 32, "../secret")):
            with self.assertRaises(importer.UnsafePath):
                importer.read_stage_blob(token, {"blob": blob})

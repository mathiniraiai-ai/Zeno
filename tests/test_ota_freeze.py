"""Updates are blocked for frozen editors (Prashanth) and unchanged for everyone else."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "service"), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient  # noqa: E402

META = {"version": "9.9.9", "sha": "abc1234", "bundle_sha256": "f" * 64, "size_bytes": 3, "notes": "n", "mandatory": True,
        "dmg_url": "", "exe_url": "", "published_by": "x", "published_at": 1.0}


class OtaFreezeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.mkdtemp(prefix="zeno_ota_freeze_")
        os.environ.update({"ZENO_SIGNUP_CODE": "team-code", "ZENO_ADMIN_PASSWORD": "admin-pw", "ZENO_DB_PATH": str(Path(tmp) / "t.db"),
                           "ZENO_RATE_PER_MIN": "200", "ZENO_MAX_SIGNUPS_PER_HOUR": "500"})
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("ZENO_OTA_FREEZE_EMAILS", None)
        os.environ.pop("ZENO_OTA_UNFREEZE_EMAILS", None)
        import app as service_app
        from keystore import KeyStore
        service_app._store = KeyStore(sqlite_path=os.environ["ZENO_DB_PATH"])
        self.svc = service_app
        self.client = TestClient(service_app.app)
        self.frozen = {"Authorization": "Bearer " + service_app.store().create("Prashanth Birlla", "prashanthbirlla@gosupernova.live")}
        self.other = {"Authorization": "Bearer " + service_app.store().create("Balesh P", "balesh@gosupernova.live")}

    def test_frozen_editor_sees_no_release_and_cannot_download(self) -> None:
        with mock.patch.object(self.svc.store(), "latest_release_meta", return_value=META), \
             mock.patch.object(self.svc.store(), "latest_release_bundle", return_value=(META, b"zip")):
            fz = self.client.get("/v1/version", headers=self.frozen).json()
            ot = self.client.get("/v1/version", headers=self.other).json()
            self.assertFalse(fz["has_release"])
            self.assertTrue(ot["has_release"])
            self.assertEqual(self.client.get("/v1/update-bundle", headers=self.frozen).status_code, 404)
            self.assertEqual(self.client.get("/v1/update-bundle", headers=self.other).status_code, 200)

    def test_env_can_freeze_more_and_unfreeze(self) -> None:
        with mock.patch.dict(os.environ, {"ZENO_OTA_FREEZE_EMAILS": "balesh@gosupernova.live"}):
            self.assertTrue(self.svc._ota_frozen(1) or self.svc._ota_frozen(2))
        with mock.patch.dict(os.environ, {"ZENO_OTA_UNFREEZE_EMAILS": "prashanthbirlla@gosupernova.live"}):
            self.assertNotIn("prashanthbirlla@gosupernova.live", self.svc._ota_frozen_emails())
        self.assertFalse(self.svc._ota_frozen(None))


if __name__ == "__main__":
    unittest.main()

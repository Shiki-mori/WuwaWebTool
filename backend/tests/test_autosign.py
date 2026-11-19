"""Offline tests for daily auto sign-in. DPAPI and schtasks are faked; never contacts Kuro."""
import base64
import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

import autosign as cli
from api.autosign import router
from core import autosign
from core.autosign import AutoSignError, AutoSignSettings
from core.game_signin import SignInResult, SignInReward

TOKEN, DID, UID = "secret-token-sentinel", "secret-did-sentinel", "123456789"


def fake_dpapi(data: bytes, protect: bool) -> bytes:
    # Reversible and visibly different from the input, like DPAPI ciphertext.
    return base64.b85encode(data[::-1]) if protect else base64.b85decode(data)[::-1]


def result(status="signed", success=True, **kw):
    return SignInResult(success=success, status=status, msg=kw.pop("msg", status), **kw)


class AutoSignBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tasks = {}
        self.patches = [
            patch.dict(os.environ, {"WUWA_DATA_PATH": self.tmp.name}),
            patch("core.autosign._dpapi", side_effect=fake_dpapi),
            patch("core.autosign.supported", return_value=True),
            patch("core.autosign.install_task", side_effect=lambda t: self.tasks.update(time=t)),
            patch("core.autosign.remove_task", side_effect=lambda: self.tasks.clear()),
            patch("core.autosign.query_task", side_effect=lambda: {
                "installed": bool(self.tasks), "time": self.tasks.get("time")}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def disk_text(self) -> str:
        return " ".join(p.read_text(encoding="utf-8") for p in autosign.data_dir().glob("*") if p.is_file())


class StorageTests(AutoSignBase):
    async def test_round_trip_encrypts_credentials_and_hides_repr(self):
        autosign.save_settings(AutoSignSettings(uid=UID, token=TOKEN, did=DID, time="08:30"))
        self.assertNotIn(TOKEN, self.disk_text())
        self.assertNotIn(DID, self.disk_text())
        loaded = autosign.load_settings()
        self.assertEqual((loaded.uid, loaded.token, loaded.did, loaded.time), (UID, TOKEN, DID, "08:30"))
        self.assertNotIn(TOKEN, repr(loaded))
        autosign.delete_settings()
        self.assertIsNone(autosign.load_settings())

    async def test_corrupt_settings_reported_without_crash(self):
        autosign.data_dir().mkdir(parents=True)
        (autosign.data_dir() / "settings.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(AutoSignError):
            autosign.load_settings()
        self.assertIn("损坏", autosign.status_payload()["msg"])

    async def test_unsupported_platform(self):
        with patch("core.autosign.supported", return_value=False):
            self.assertFalse(autosign.status_payload()["supported"])


class TaskXmlTests(unittest.TestCase):
    def test_xml_escapes_paths_and_enables_catch_up(self):
        xml = autosign.build_task_xml("07:05", "S-1-5-21-1", (r"D:\A&B\pythonw.exe", '"D:\\A&B\\autosign.py"', r"D:\A&B"),
                                      today="2026-11-19")
        self.assertIn("<StartBoundary>2026-11-19T07:05:00</StartBoundary>", xml)
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", xml)
        self.assertIn(r"D:\A&amp;B\pythonw.exe", xml)
        self.assertIn("<LogonType>InteractiveToken</LogonType>", xml)
        for bad in ("7:05", "24:00", "07:60", "", "07:05<"):
            with self.assertRaises(AutoSignError):
                autosign.build_task_xml(bad, "S-1", ("a", "b", "c"))

    def test_query_reads_actual_trigger_time(self):
        xml = autosign.build_task_xml("21:40", "S-1", ("a", "b", "c"))
        with patch("core.autosign.supported", return_value=True), \
                patch("core.autosign._schtasks", return_value=(True, xml)):
            self.assertEqual(autosign.query_task(), {"installed": True, "time": "21:40"})
        with patch("core.autosign.supported", return_value=True), \
                patch("core.autosign._schtasks", return_value=(False, "ERROR")):
            self.assertEqual(autosign.query_task(), {"installed": False, "time": None})


class RunTests(AutoSignBase):
    def settings(self):
        return AutoSignSettings(uid=UID, token=TOKEN, did=DID, time="09:00")

    async def test_retries_transient_failure_then_stops(self):
        sign = AsyncMock(side_effect=[result("network_error", False), result("signed", signed_days=3)])
        sleep = AsyncMock()
        with patch("core.autosign.game_signin", sign):
            res, attempts = await autosign.run_once(self.settings(), sleep=sleep)
        self.assertEqual((res.status, attempts), ("signed", 2))
        self.assertTrue(sign.await_args.kwargs["submit"])
        self.assertEqual(sleep.await_count, 1)

    async def test_no_retry_for_login_expired_or_already_signed(self):
        for status, ok in (("login_expired", False), ("already_signed", True), ("captcha_required", False)):
            sign = AsyncMock(return_value=result(status, ok))
            with patch("core.autosign.game_signin", sign):
                _, attempts = await autosign.run_once(self.settings(), sleep=AsyncMock())
            self.assertEqual(attempts, 1)

    async def test_gives_up_after_retries(self):
        sign = AsyncMock(return_value=result("network_error", False))
        with patch("core.autosign.game_signin", sign):
            res, attempts = await autosign.run_once(self.settings(), sleep=AsyncMock())
        self.assertEqual((res.status, attempts), ("network_error", 3))

    async def test_cli_without_settings_is_noop(self):
        with patch("core.autosign.game_signin", AsyncMock()) as sign:
            self.assertEqual(await cli.run(), 0)
        sign.assert_not_awaited()
        self.assertIn("未开启", autosign.log_path().read_text(encoding="utf-8"))

    async def test_cli_records_result_without_credentials(self):
        autosign.save_settings(self.settings())
        signed = result("signed", signed_days=7, rewards=[SignInReward(name="星声", count=20)])
        with patch("core.autosign.game_signin", AsyncMock(return_value=signed)):
            self.assertEqual(await cli.run(), 0)
        last = json.loads((autosign.data_dir() / "last_run.json").read_text(encoding="utf-8"))
        self.assertEqual((last["uid"], last["status"], last["signed_days"]), (UID, "signed", 7))
        self.assertIn("星声×20", autosign.log_path().read_text(encoding="utf-8"))
        self.assertNotIn(TOKEN, self.disk_text())
        self.assertEqual(autosign.status_payload()["last_run"]["status"], "signed")
        with patch("core.autosign.game_signin", AsyncMock(return_value=result("login_expired", False))):
            self.assertEqual(await cli.run(), 1)


class ApiTests(AutoSignBase):
    def setUp(self):
        super().setUp()
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/game/auto-sign")
        self.verify = AsyncMock(return_value=result("unsigned", signed_today=False))
        self.patches.append(patch("api.autosign.game_signin", self.verify))
        self.patches[-1].start()

    def client(self, client_addr=("127.0.0.1", 50000)):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=client_addr),
                                 base_url="http://localhost:8000")

    def body(self, **kw):
        return {"uid": UID, "token": TOKEN, "did": DID, "time": "08:15", **kw}

    async def test_refuses_remote_proxied_and_cross_site(self):
        async with self.client(("192.168.1.20", 50000)) as c:
            self.assertEqual((await c.get("/api/game/auto-sign/status")).status_code, 403)
        async with self.client() as c:
            r = await c.post("/api/game/auto-sign/enable", json=self.body(), headers={"X-Forwarded-For": "127.0.0.1"})
            self.assertEqual(r.status_code, 403)
            r = await c.post("/api/game/auto-sign/disable", headers={"Origin": "https://evil.example"})
            self.assertEqual(r.status_code, 403)
            r = await c.post("/api/game/auto-sign/disable", headers={"Origin": "http://localhost.evil.example:8000"})
            self.assertEqual(r.status_code, 403)
            for origin in ("http://localhost:8000", "http://localhost:5173", "http://127.0.0.1:5173"):
                r = await c.get("/api/game/auto-sign/status", headers={"Origin": origin})
                self.assertEqual(r.status_code, 200)
        self.assertFalse(autosign.settings_exist())

    async def test_enable_verifies_read_only_then_saves_and_schedules(self):
        async with self.client() as c:
            r = (await c.post("/api/game/auto-sign/enable", json=self.body())).json()
        self.assertTrue(r["success"])
        self.assertEqual(self.verify.await_args.kwargs, {})  # status check only, never submit=True
        self.assertEqual(self.tasks, {"time": "08:15"})
        self.assertEqual((r["status"]["uid"], r["status"]["time"], r["status"]["task_time"]), (UID, "08:15", "08:15"))
        self.assertNotIn(TOKEN, json.dumps(r))

    async def test_enable_rejected_by_upstream_or_bad_input_saves_nothing(self):
        self.verify.return_value = result("login_expired", False, msg="登录已过期，请重新登录")
        async with self.client() as c:
            r = (await c.post("/api/game/auto-sign/enable", json=self.body())).json()
            self.assertFalse(r["success"])
            self.assertIn("登录已过期", r["msg"])
            r = (await c.post("/api/game/auto-sign/enable", json=self.body(time="9:00"))).json()
            self.assertFalse(r["success"])
            bad = await c.post("/api/game/auto-sign/enable", json={"uid": UID, "token": {"x": TOKEN}, "did": DID})
            self.assertEqual(bad.status_code, 422)
            self.assertNotIn(TOKEN, bad.text)
        self.assertFalse(autosign.settings_exist())
        self.assertEqual(self.tasks, {})

    async def test_failed_task_install_rolls_back_new_settings(self):
        with patch("core.autosign.install_task", side_effect=AutoSignError("创建计划任务失败：拒绝访问")):
            async with self.client() as c:
                r = (await c.post("/api/game/auto-sign/enable", json=self.body())).json()
        self.assertFalse(r["success"])
        self.assertIn("拒绝访问", r["msg"])
        self.assertFalse(autosign.settings_exist())

    async def test_sync_updates_only_same_uid_and_only_when_changed(self):
        async with self.client() as c:
            await c.post("/api/game/auto-sign/enable", json=self.body())
            self.verify.reset_mock()
            await c.post("/api/game/auto-sign/sync", json=self.body())
            self.verify.assert_not_awaited()  # unchanged: no upstream call
            await c.post("/api/game/auto-sign/sync", json=self.body(uid="987654321", token="other"))
            self.assertEqual(autosign.load_settings().token, TOKEN)
            r = (await c.post("/api/game/auto-sign/sync", json=self.body(token="new-token"))).json()
            self.assertTrue(r["success"])
            self.assertEqual(autosign.load_settings().token, "new-token")
            self.assertEqual(autosign.load_settings().time, "08:15")
            self.verify.return_value = result("login_expired", False, msg="登录已过期")
            r = (await c.post("/api/game/auto-sign/sync", json=self.body(token="stale"))).json()
            self.assertFalse(r["success"])
            self.assertEqual(autosign.load_settings().token, "new-token")

    async def test_disable_removes_task_and_credentials(self):
        async with self.client() as c:
            await c.post("/api/game/auto-sign/enable", json=self.body())
            autosign.write_last_run(result("signed"), UID, 1)
            r = (await c.post("/api/game/auto-sign/disable")).json()
        self.assertTrue(r["success"])
        self.assertFalse(r["status"]["enabled"])
        self.assertEqual(self.tasks, {})
        self.assertEqual([p for p in autosign.data_dir().glob("*") if p.is_file()], [])


if __name__ == "__main__":
    unittest.main()

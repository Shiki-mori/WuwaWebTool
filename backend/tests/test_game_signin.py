"""Offline tests. Never contact Kuro or use browser credentials."""
import asyncio
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from api.signin import router
from core.api.api import ROLE_LIST_URL, SERVER_ID, SIGNIN_TASK_LIST_URL, SIGNIN_URL
from core.api.requests import WavesApi
from core.game_signin import SignInAuth, game_signin


def response(code=200, data=None):
    return SimpleNamespace(code=code, data=data, success=code in (0, 200), is_token_invalid=code == 220)


class GameSignInTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth = SignInAuth(uid="123456789", token="test-token-private", did="test-device-private")
        self.roles = [{"gameId": 2, "roleId": "111111111", "serverId": "1000"},
                      {"gameId": 3, "roleId": self.auth.uid, "serverId": SERVER_ID}]
        self.signed = False
        self.writes = 0
        self.calls = []
        self.fail_url = None
        self.fail_value = None
        self.receipt = None

        async def upstream(url, method, headers, **kwargs):
            self.calls.append((url, method, headers, kwargs))
            if url == self.fail_url:
                if isinstance(self.fail_value, Exception):
                    raise self.fail_value
                return self.fail_value
            if url == ROLE_LIST_URL:
                return response(data=self.roles)
            if url == SIGNIN_TASK_LIST_URL:
                return response(data={"isSigIn": self.signed, "sigInNum": 5 + self.writes,
                                      "signInGoodsConfigs": [{"goodsName": "Not a receipt"}]})
            if url == SIGNIN_URL:
                self.writes += 1
                self.signed = True
                return response(data=self.receipt)
            raise AssertionError("Unexpected endpoint")

        self.upstream = upstream
        self.patch = patch("core.game_signin.waves_api._waves_request", side_effect=upstream)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    async def test_status_never_writes_or_invents_rewards(self):
        result = await game_signin(self.auth)
        self.assertTrue(result.success)
        self.assertFalse(result.signed_today)
        self.assertEqual(result.status, "unsigned")
        self.assertEqual(result.rewards, [])
        self.assertEqual(self.writes, 0)

    async def test_success_targets_selected_wuwa_not_first_game(self):
        self.receipt = {"goodsName": "星声", "goodsNum": 20}
        result = await game_signin(self.auth, submit=True)
        self.assertEqual(result.status, "signed")
        self.assertTrue(result.signed_today)
        self.assertEqual(result.signed_days, 6)
        self.assertEqual(result.rewards[0].count, 20)
        writes = [call for call in self.calls if call[0] == SIGNIN_URL]
        self.assertEqual(len(writes), 1)
        data = writes[0][3]["data"]
        self.assertEqual(data["gameId"], 3)
        self.assertEqual(data["roleId"], self.auth.uid)
        self.assertEqual(data["serverId"], SERVER_ID)
        self.assertEqual(len(data["reqMonth"]), 2)
        self.assertNotIn("userId", data)
        for _, _, headers, kwargs in self.calls:
            self.assertEqual(headers["token"], self.auth.token)
            self.assertEqual(headers["devCode"], self.auth.did)
            self.assertEqual(kwargs["max_retries"], 1)
            self.assertFalse(kwargs["allow_captcha"])
            self.assertTrue(kwargs["strict_response"])

    async def test_success_with_no_receipt_has_no_rewards(self):
        result = await game_signin(self.auth, submit=True)
        self.assertTrue(result.success)
        self.assertEqual(result.rewards, [])

    async def test_already_signed_skips_write(self):
        self.signed = True
        result = await game_signin(self.auth, submit=True)
        self.assertEqual(result.status, "already_signed")
        self.assertEqual(self.writes, 0)

    async def test_race_already_signed_1511(self):
        self.fail_url, self.fail_value = SIGNIN_URL, response(1511)
        result = await game_signin(self.auth, submit=True)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "already_signed")

    async def test_expired_during_role_status_and_write(self):
        for url in (ROLE_LIST_URL, SIGNIN_TASK_LIST_URL, SIGNIN_URL):
            with self.subTest(url=url):
                self.fail_url, self.fail_value = url, response(220)
                result = await game_signin(self.auth, submit=True)
                self.assertFalse(result.success)
                self.assertIsNone(result.signed_today)
                self.assertEqual(result.status, "login_expired")

    async def test_unbound_foreign_game_and_overseas_rejected(self):
        for roles in ([], [{"gameId": 2, "roleId": self.auth.uid}],
                      [{"gameId": 3, "roleId": self.auth.uid, "serverId": "overseas"}]):
            self.roles = roles
            result = await game_signin(self.auth, submit=True)
            self.assertEqual(result.status, "invalid_role")
        self.assertEqual(self.writes, 0)

    async def test_supplied_wrong_server_rejected(self):
        self.auth.server_id = "1000"
        result = await game_signin(self.auth, submit=True)
        self.assertEqual(result.status, "invalid_role")
        self.assertEqual(self.writes, 0)

    async def test_nested_role_list_and_missing_game_id(self):
        self.roles = {"roleList": [{"roleId": self.auth.uid}]}
        self.assertEqual((await game_signin(self.auth)).status, "unsigned")

    async def test_1513_and_unknown_failure_do_not_report_signed(self):
        for code, status in ((1513, "invalid_role"), (270, "error"), (500, "error")):
            self.fail_url, self.fail_value = SIGNIN_URL, response(code)
            result = await game_signin(self.auth, submit=True)
            self.assertEqual(result.status, status)
            self.assertFalse(result.success)
            self.assertIsNone(result.signed_today)

    async def test_captcha_does_not_trigger_solver(self):
        self.fail_url, self.fail_value = SIGNIN_TASK_LIST_URL, response(104, {"geeTest": True})
        self.assertEqual((await game_signin(self.auth, submit=True)).status, "captcha_required")
        self.assertEqual(self.writes, 0)

    async def test_malformed_calendar_cannot_authorize_write(self):
        for data in (None, {}, {"isSigIn": "false"}, {"isSigIn": 2}):
            self.fail_url, self.fail_value = SIGNIN_TASK_LIST_URL, response(data=data)
            result = await game_signin(self.auth, submit=True)
            self.assertFalse(result.success)
            self.assertIsNone(result.signed_today)
        self.assertEqual(self.writes, 0)

    async def test_network_failure_before_and_during_write(self):
        for url in (ROLE_LIST_URL, SIGNIN_URL):
            self.fail_url, self.fail_value = url, TimeoutError("private data must not escape")
            result = await game_signin(self.auth, submit=True)
            self.assertEqual(result.status, "network_error")
            self.assertNotIn("private data", result.msg)
            self.assertIsNone(result.signed_today)
        self.assertEqual(sum(call[0] == SIGNIN_URL for call in self.calls), 1)

    async def test_post_read_failure_preserves_acknowledged_success(self):
        async def fail_after_write(*args, **kwargs):
            if args[0] == SIGNIN_TASK_LIST_URL and self.writes:
                raise TimeoutError()
            return await self.upstream(*args, **kwargs)
        with patch("core.game_signin.waves_api._waves_request", side_effect=fail_after_write):
            result = await game_signin(self.auth, submit=True)
        self.assertEqual(result.status, "signed")
        self.assertTrue(result.signed_today)

    async def test_parallel_submissions_only_write_once(self):
        results = await asyncio.gather(game_signin(self.auth, submit=True), game_signin(self.auth, submit=True))
        self.assertEqual(self.writes, 1)
        self.assertEqual({r.status for r in results}, {"signed", "already_signed"})

    async def test_endpoints_and_validation_redact_credentials(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/game")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            bad = await client.post("/api/game/sign-in", json={"uid": "123", "token": {"secret": self.auth.token}, "did": self.auth.did})
            self.assertEqual(bad.status_code, 422)
            self.assertNotIn(self.auth.token, bad.text)
            self.assertNotIn(self.auth.did, bad.text)
            status = await client.post("/api/game/sign-in/status", json=self.auth.model_dump())
            self.assertEqual(status.json()["status"], "unsigned")
            signed = await client.post("/api/game/sign-in", json=self.auth.model_dump())
            self.assertEqual(signed.json()["status"], "signed")
            self.assertEqual(self.writes, 1)
        self.assertNotIn(self.auth.token, repr(self.auth))


class RequestIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_upstream_never_defaults_to_success_and_no_write_retry(self):
        api = WavesApi()
        for payload in ({}, {"code": "200"}, {"code": 200, "success": False}, []):
            response_obj = SimpleNamespace(raise_for_status=lambda: None, json=AsyncMock(return_value=payload))
            context = AsyncMock()
            context.__aenter__.return_value = response_obj
            session = SimpleNamespace(request=lambda *args, **kwargs: context)
            with patch.object(api, "get_session", AsyncMock(return_value=session)), self.assertLogs("wuwa_web", logging.WARNING) as captured:
                with self.assertRaises(TypeError):
                    await api._waves_request(SIGNIN_URL, "POST", {"token": "secret-sentinel"},
                                             data={"roleId": "private-role"}, max_retries=1, strict_response=True, allow_captcha=False)
            self.assertEqual(response_obj.json.await_count, 1)
            self.assertNotIn("secret-sentinel", " ".join(captured.output))
            self.assertNotIn("private-role", " ".join(captured.output))


if __name__ == "__main__":
    unittest.main()

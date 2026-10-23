"""Manual CN Wuthering Waves rewards. Protocol references: docs/game-signin.md."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Literal
from weakref import WeakValueDictionary

from pydantic import BaseModel, Field

from core.api.api import ROLE_LIST_URL, SERVER_ID, SIGNIN_TASK_LIST_URL, SIGNIN_URL
from core.api.request_util import KuroApiResp, get_base_header
from core.waves_api_wrapper import waves_api

CN_TIME = timezone(timedelta(hours=8))
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


class SignInAuth(BaseModel):
    uid: str = Field(min_length=1, max_length=32)
    token: str = Field(min_length=1, max_length=8192, repr=False)
    did: str = Field(min_length=1, max_length=512, repr=False)
    server_id: str = ""


class SignInReward(BaseModel):
    name: str
    count: int | None = None


class SignInResult(BaseModel):
    success: bool = False
    status: Literal["unsigned", "signed", "already_signed", "login_expired", "invalid_role",
                    "network_error", "captcha_required", "error"] = "error"
    msg: str
    signed_today: bool | None = None
    signed_days: int | None = None
    checked_date: str = Field(default_factory=lambda: datetime.now(CN_TIME).date().isoformat())
    rewards: list[SignInReward] = Field(default_factory=list)


def _failure(result: KuroApiResp) -> SignInResult:
    # Do not echo arbitrary upstream messages: they may contain private data or HTML.
    if result.is_token_invalid:
        return SignInResult(status="login_expired", msg="登录已过期，请重新登录")
    if result.code == 104 or isinstance(result.data, dict) and result.data.get("geeTest") is True:
        return SignInResult(status="captcha_required", msg="官方接口需要安全验证，请在库街区完成验证后重试")
    if result.code == 1513:
        return SignInResult(status="invalid_role", msg="游戏角色信息异常，请刷新角色绑定信息")
    return SignInResult(msg=f"库街区请求失败（代码 {result.code}），请稍后刷新状态")


async def _request(url: str, auth: SignInAuth, data: dict) -> KuroApiResp:
    headers = await get_base_header(auth.did)
    headers.update({"token": auth.token, "did": auth.did})
    # No automatic write retries, CAPTCHA solvers, third-party services or database credentials.
    return await waves_api._waves_request(
        url, "POST", headers, data=data, max_retries=1,
        strict_response=True, allow_captcha=False,
    )


async def _bound_role(auth: SignInAuth) -> dict | SignInResult:
    result = await _request(ROLE_LIST_URL, auth, {"gameId": 3})
    if not result.success:
        return _failure(result)
    roles = result.data
    if isinstance(roles, dict):
        roles = roles.get("roleList", roles.get("list"))
    if not isinstance(roles, list):
        return SignInResult(msg="角色列表响应异常，请刷新后重试")
    for role in roles:
        if not isinstance(role, dict) or str(role.get("gameId", 3)) != "3":
            continue
        if str(role.get("roleId")) != auth.uid:
            continue
        server = str(role.get("serverId") or SERVER_ID)
        if server != SERVER_ID or (auth.server_id and auth.server_id != server):
            break
        if not auth.uid.isdecimal() or not 0 < int(auth.uid) < 200000000:
            break
        # userId is deliberately not needed by RoverSign's protocol; roleId is the game UID.
        return {"gameId": 3, "roleId": auth.uid, "serverId": server}
    return SignInResult(status="invalid_role", msg="请选择当前账号绑定的鸣潮国服角色后重试")


def _calendar(result: KuroApiResp) -> SignInResult:
    if not result.success:
        return _failure(result)
    data = result.data
    if not isinstance(data, dict) or type(data.get("isSigIn")) not in (bool, int) or data["isSigIn"] not in (True, False):
        return SignInResult(msg="签到状态响应异常，请刷新后重试")
    signed = bool(data["isSigIn"])
    days = data.get("sigInNum")
    return SignInResult(
        success=True, status="already_signed" if signed else "unsigned",
        msg="今日已签到" if signed else "今日未签到", signed_today=signed,
        signed_days=days if type(days) is int and days >= 0 else None,
    )


def _rewards(data: object) -> list[SignInReward]:
    # Only explicit goods returned by the write response. Calendar configurations are not receipts.
    if isinstance(data, dict):
        data = [data] if "goodsName" in data else data.get("rewardList", [])
    if not isinstance(data, list):
        return []
    rewards = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("goodsName"), str) and item["goodsName"].strip():
            count = item.get("goodsNum")
            rewards.append(SignInReward(name=item["goodsName"], count=count if type(count) is int and count > 0 else None))
    return rewards


async def game_signin(auth: SignInAuth, *, submit: bool = False) -> SignInResult:
    key = hashlib.sha256(f"{auth.token}:{auth.uid}".encode()).hexdigest()
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        write_started = False
        try:
            role = await _bound_role(auth)
            if isinstance(role, SignInResult):
                return role
            before = _calendar(await _request(SIGNIN_TASK_LIST_URL, auth, role))
            if not submit or not before.success or before.signed_today:
                return before
            write_started = True
            result = await _request(SIGNIN_URL, auth, {
                **role, "reqMonth": datetime.now(CN_TIME).strftime("%m"),
            })
            if result.code == 1511:
                return SignInResult(success=True, status="already_signed", msg="今日已签到", signed_today=True)
            if not result.success:
                return _failure(result)
            outcome = SignInResult(
                success=True, status="signed", msg="鸣潮游戏奖励签到成功", signed_today=True,
                rewards=_rewards(result.data),
            )
            # A failed follow-up read must not undo an acknowledged successful write.
            try:
                after = _calendar(await _request(SIGNIN_TASK_LIST_URL, auth, role))
                if after.success:
                    outcome.signed_days = after.signed_days
            except Exception:
                pass
            return outcome
        except Exception:
            return SignInResult(
                status="network_error",
                msg="签到结果暂未确认，请先刷新状态再决定是否重试" if write_started else "网络或服务响应异常，请稍后刷新状态",
            )

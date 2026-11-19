"""Daily auto sign-in settings. Local machine only: see core/autosign.py."""
from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.signin import PrivateValidationRoute
from core import autosign
from core.autosign import AutoSignError, AutoSignSettings
from core.game_signin import SignInAuth, game_signin
from core.logger import logger

_PROXY_HEADERS = (b"x-forwarded-for", b"x-forwarded-host", b"x-real-ip", b"forwarded")


def local_only(request: Request) -> None:
    """Credentials are written to this machine, so only the local browser may manage them.

    uvicorn runs with forwarded_allow_ips="*", which rewrites the client address from
    X-Forwarded-For; any proxy header therefore means "not provably local" and is refused.
    Cross-site pages could reach localhost through the permissive CORS config, so an
    Origin header must be this host or another loopback origin.
    """
    headers = dict(request.scope.get("headers") or [])
    host = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        loopback = host in ("localhost", "testclient")
    if not loopback or any(h in headers for h in _PROXY_HEADERS):
        raise HTTPException(403, "自动签到只能在运行本服务的电脑上，通过 localhost 设置")
    origin = request.headers.get("origin")
    if origin:
        parts = urlsplit(origin)
        # Same origin, or another local page such as the Vite dev server on :5173.
        if parts.netloc != request.headers.get("host", "") and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise HTTPException(403, "跨站请求被拒绝")


router = APIRouter(route_class=PrivateValidationRoute, dependencies=[Depends(local_only)])
_lock = asyncio.Lock()


class EnableRequest(SignInAuth):
    time: str = Field(default="09:00", max_length=5)


class SyncRequest(SignInAuth):
    pass


class AutoSignResponse(BaseModel):
    success: bool
    msg: str = ""
    status: dict


async def _status() -> dict:
    return await asyncio.to_thread(autosign.status_payload)


async def _verify(auth: SignInAuth) -> str | None:
    """Read-only check that the credentials work for this UID. Returns an error message."""
    result = await game_signin(auth)
    return None if result.success else result.msg


@router.get("/status", response_model=AutoSignResponse)
async def status():
    return AutoSignResponse(success=True, status=await _status())


@router.post("/enable", response_model=AutoSignResponse)
async def enable(req: EnableRequest):
    if not autosign.supported():
        return AutoSignResponse(success=False, msg="自动签到目前仅支持 Windows", status=await _status())
    if not autosign.valid_time(req.time):
        return AutoSignResponse(success=False, msg="签到时间格式应为 HH:MM", status=await _status())
    async with _lock:
        error = await _verify(req)
        if error:
            return AutoSignResponse(success=False, msg=f"未开启：{error}", status=await _status())
        existed = autosign.settings_exist()
        try:
            await asyncio.to_thread(autosign.save_settings, AutoSignSettings(
                uid=req.uid, token=req.token, did=req.did, time=req.time))
            await asyncio.to_thread(autosign.install_task, req.time)
        except AutoSignError as e:
            if not existed:
                autosign.delete_settings()
            return AutoSignResponse(success=False, msg=str(e), status=await _status())
    logger.info(f"[autosign] enabled at {req.time}")
    return AutoSignResponse(success=True, msg=f"已开启，每天 {req.time} 自动签到", status=await _status())


@router.post("/sync", response_model=AutoSignResponse)
async def sync(req: SyncRequest):
    """Called by the page after login: refresh stored credentials for the same UID."""
    async with _lock:
        try:
            current = await asyncio.to_thread(autosign.load_settings)
        except AutoSignError:
            current = None  # unreadable: let the user re-enable explicitly
        if current is None or current.uid != req.uid:
            return AutoSignResponse(success=True, msg="", status=await _status())
        if current.token == req.token and current.did == req.did:
            return AutoSignResponse(success=True, msg="", status=await _status())
        error = await _verify(req)
        if error:
            return AutoSignResponse(success=False, msg=f"登录信息未同步：{error}", status=await _status())
        current.token, current.did = req.token, req.did
        try:
            await asyncio.to_thread(autosign.save_settings, current)
        except AutoSignError as e:
            return AutoSignResponse(success=False, msg=str(e), status=await _status())
    logger.info("[autosign] credentials refreshed from browser login")
    return AutoSignResponse(success=True, msg="已同步最新登录信息", status=await _status())


@router.post("/disable", response_model=AutoSignResponse)
async def disable():
    async with _lock:
        try:
            if autosign.supported():
                await asyncio.to_thread(autosign.remove_task)
        except AutoSignError as e:
            return AutoSignResponse(success=False, msg=str(e), status=await _status())
        autosign.delete_settings()
    logger.info("[autosign] disabled")
    return AutoSignResponse(success=True, msg="已关闭自动签到，并删除本机保存的登录信息", status=await _status())

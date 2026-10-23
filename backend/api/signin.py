"""Manual game reward endpoints. No scheduled or community actions."""
from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse

from core.game_signin import SignInAuth, SignInResult, game_signin


class PrivateValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                # FastAPI's default validation detail includes submitted values (possibly tokens).
                return JSONResponse(status_code=422, content={
                    "success": False, "status": "error", "msg": "登录或角色参数不完整，请重新登录并选择角色",
                })
        return private_handler


router = APIRouter(route_class=PrivateValidationRoute)


@router.post("/sign-in/status", response_model=SignInResult)
async def signin_status(auth: SignInAuth):
    return await game_signin(auth)


@router.post("/sign-in", response_model=SignInResult)
async def signin(auth: SignInAuth):
    return await game_signin(auth, submit=True)

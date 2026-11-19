"""Opt-in daily CN Wuwa reward sign-in, triggered by Windows Task Scheduler.

Credentials come from the browser login the user already has (no second login). They are
stored only after the user enables auto sign-in, encrypted with Windows DPAPI (current user).
The scheduled task runs ``backend/autosign.py`` once per day; the web service need not be running.
See docs/game-signin.md.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from core.game_signin import CN_TIME, SignInAuth, SignInResult, game_signin
from core.resource.RESOURCE_PATH import _get_data_path

TASK_NAME = "WuwaWebTool 鸣潮每日签到"
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "autosign.py"
_ENTROPY = b"WuwaWebTool-autosign-v1"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# Retryable outcomes. The sign-in always reads today's status before writing, so a retry
# after an unconfirmed write cannot claim twice.
RETRYABLE = {"network_error", "error"}


class AutoSignError(RuntimeError):
    """User-facing failure. Messages never contain credentials."""


def supported() -> bool:
    return sys.platform == "win32"


def valid_time(value: str) -> bool:
    return bool(_TIME_RE.match(value or ""))


def data_dir() -> Path:
    return _get_data_path() / "autosign"


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def _last_run_path() -> Path:
    return data_dir() / "last_run.json"


def log_path() -> Path:
    return data_dir() / "autosign.log"


# ─── DPAPI (ctypes, no extra dependency) ───

def _dpapi(data: bytes, protect: bool) -> bytes:
    if not supported():
        raise AutoSignError("自动签到目前仅支持 Windows")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(raw: bytes):
        buf = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    in_blob, _in_buf = blob(data)
    ent_blob, _ent_buf = blob(_ENTROPY)
    out = Blob()
    ui_forbidden = 0x1
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(in_blob), None, ctypes.byref(ent_blob),
                                      None, None, ui_forbidden, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, ctypes.byref(ent_blob),
                                        None, None, ui_forbidden, ctypes.byref(out))
    if not ok:
        raise AutoSignError(f"Windows 凭证{'加密' if protect else '解密'}失败（{ctypes.get_last_error()}）")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(text: str) -> str:
    return base64.b64encode(_dpapi(text.encode(), True)).decode()


def unprotect(text: str) -> str:
    return _dpapi(base64.b64decode(text), False).decode()


# ─── Storage ───

@dataclass
class AutoSignSettings:
    uid: str
    token: str
    did: str
    time: str
    updated_at: str = ""

    def auth(self) -> SignInAuth:
        return SignInAuth(uid=self.uid, token=self.token, did=self.did)

    def __repr__(self) -> str:  # never leak credentials into logs or tracebacks
        return f"AutoSignSettings(uid={self.uid!r}, time={self.time!r})"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def settings_exist() -> bool:
    return _settings_path().exists()


def load_settings() -> AutoSignSettings | None:
    """None when disabled. Raises AutoSignError when present but unreadable."""
    path = _settings_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return AutoSignSettings(
            uid=str(raw["uid"]), token=unprotect(raw["token"]), did=unprotect(raw["did"]),
            time=str(raw["time"]), updated_at=str(raw.get("updated_at", "")),
        )
    except AutoSignError:
        raise
    except Exception:
        raise AutoSignError("自动签到配置损坏，请关闭后重新开启")


def save_settings(settings: AutoSignSettings) -> None:
    settings.updated_at = datetime.now(CN_TIME).isoformat(timespec="seconds")
    _atomic_write(_settings_path(), json.dumps({
        "version": 1, "uid": settings.uid, "time": settings.time, "updated_at": settings.updated_at,
        "protection": "dpapi-current-user",
        "token": protect(settings.token), "did": protect(settings.did),
    }, ensure_ascii=False, indent=2))


def delete_settings() -> None:
    for path in (_settings_path(), _last_run_path()):
        path.unlink(missing_ok=True)


def read_last_run() -> dict | None:
    try:
        data = json.loads(_last_run_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def write_last_run(result: SignInResult, uid: str, attempts: int) -> dict:
    record = {
        "time": datetime.now(CN_TIME).isoformat(timespec="seconds"),
        "date": result.checked_date, "uid": uid, "attempts": attempts,
        "success": result.success, "status": result.status, "msg": result.msg,
        "signed_days": result.signed_days,
        "rewards": [r.model_dump() for r in result.rewards],
    }
    _atomic_write(_last_run_path(), json.dumps(record, ensure_ascii=False, indent=2))
    return record


def append_log(line: str) -> None:
    try:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        with log_path().open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now(CN_TIME).isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


# ─── Windows Task Scheduler ───

def _schtasks(*args: str) -> tuple[bool, str]:
    if not supported():
        raise AutoSignError("自动签到目前仅支持 Windows")
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    exe = str(Path(system_root) / "System32" / "schtasks.exe")
    try:
        proc = subprocess.run([exe, *args], capture_output=True, timeout=30,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AutoSignError(f"无法调用 Windows 任务计划程序：{type(e).__name__}")
    out = proc.stdout or proc.stderr

    def decode(raw: bytes) -> str:
        # /XML output is UTF-16; text messages use the console code page.
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16")
        if b"\x00" in raw[:200]:
            return raw.decode("utf-16-le", "replace")
        for enc in ("oem", "mbcs", "utf-8"):
            try:
                return raw.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", "replace")

    text = decode(proc.stdout) if proc.returncode == 0 else decode(proc.stderr or out)
    return proc.returncode == 0, text.strip()


def _current_user_sid() -> str:
    proc = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, timeout=15,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.search(rb"S-1-[0-9-]+", proc.stdout or b"")
    if proc.returncode != 0 or not match:
        raise AutoSignError("无法识别当前 Windows 用户")
    return match.group().decode()


def task_command() -> tuple[str, str, str]:
    """(interpreter, arguments, working dir). pythonw avoids flashing a console window."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe
    return str(interpreter), f'"{SCRIPT_PATH}"', str(SCRIPT_PATH.parent)


def build_task_xml(time: str, sid: str, command: tuple[str, str, str], today: str | None = None) -> str:
    if not valid_time(time):
        raise AutoSignError("签到时间格式应为 HH:MM")
    day = today or datetime.now().strftime("%Y-%m-%d")
    exe, args, workdir = command
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>WuwaWebTool：每天领取鸣潮国服游戏签到奖励（库街区）。</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{day}T{time}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(sid)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install_task(time: str) -> None:
    xml = build_task_xml(time, _current_user_sid(), task_command())
    path = data_dir() / "scheduled-task.xml"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(xml, encoding="utf-16")
        ok, msg = _schtasks("/Create", "/TN", TASK_NAME, "/XML", str(path), "/F")
    finally:
        path.unlink(missing_ok=True)
    if not ok:
        raise AutoSignError(f"创建计划任务失败：{msg[:200]}")


def remove_task() -> None:
    ok, msg = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if not ok and query_task()["installed"]:
        raise AutoSignError(f"删除计划任务失败：{msg[:200]}")


def query_task() -> dict:
    """{"installed": bool, "time": "HH:MM" | None} — reads the actual trigger, not our config."""
    if not supported():
        return {"installed": False, "time": None}
    ok, text = _schtasks("/Query", "/TN", TASK_NAME, "/XML")
    if not ok:
        return {"installed": False, "time": None}
    match = re.search(r"<StartBoundary>[^<]*T(\d{2}:\d{2})", text)
    return {"installed": True, "time": match.group(1) if match else None}


# ─── Run ───

async def run_once(settings: AutoSignSettings, *, retry_delays: tuple[float, ...] = (120, 300), sleep=None) -> tuple[SignInResult, int]:
    """Sign in once with in-process retries for transient failures. Returns (result, attempts)."""
    import asyncio
    sleep = sleep or asyncio.sleep
    attempts = 0
    result = SignInResult(msg="未执行")
    for delay in (0, *retry_delays):
        if delay:
            await sleep(delay)
        attempts += 1
        result = await game_signin(settings.auth(), submit=True)
        if result.status not in RETRYABLE:
            break
    return result, attempts


def status_payload() -> dict:
    payload: dict = {"supported": supported(), "enabled": False, "uid": None, "time": None,
                     "task_installed": False, "task_time": None, "last_run": None, "msg": ""}
    if not supported():
        payload["msg"] = "自动签到目前仅支持在 Windows 本机运行"
        return payload
    try:
        settings = load_settings()
    except AutoSignError as e:
        payload.update(enabled=True, msg=str(e))
        settings = None
    if settings:
        payload.update(enabled=True, uid=settings.uid, time=settings.time)
    try:
        task = query_task()
        payload.update(task_installed=task["installed"], task_time=task["time"])
    except AutoSignError as e:
        payload["msg"] = str(e)
    last = read_last_run()
    if last and settings and last.get("uid") == settings.uid:
        payload["last_run"] = last
    return payload


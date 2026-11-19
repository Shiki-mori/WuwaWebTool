"""Run the daily Wuwa reward sign-in once (invoked by Windows Task Scheduler).

    python autosign.py              sign in with the credentials saved from the web page
    python autosign.py --self-test  check DPAPI and Task Scheduler access, no network writes

Exit code: 0 = signed / already signed / nothing to do, 1 = failed (see data/autosign/autosign.log).
"""
import asyncio
import os
import sys
from pathlib import Path

# pythonw has no console: give logging somewhere harmless to write.
if sys.stdout is None or sys.stderr is None:
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Same proxy handling as main.py (aiohttp may leak them despite trust_env=False).
for _key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "NO_PROXY"):
    os.environ.pop(_key, None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import autosign  # noqa: E402
from core.autosign import AutoSignError  # noqa: E402


async def run() -> int:
    try:
        settings = autosign.load_settings()
    except AutoSignError as e:
        autosign.append_log(f"跳过：{e}")
        return 1
    if settings is None:
        autosign.append_log("跳过：未开启自动签到（请在网页签到卡片中开启）")
        return 0
    result, attempts = await autosign.run_once(settings)
    autosign.write_last_run(result, settings.uid, attempts)
    days = f"，本期累计 {result.signed_days} 天" if result.signed_days is not None else ""
    rewards = "、".join(f"{r.name}×{r.count}" if r.count else r.name for r in result.rewards)
    autosign.append_log(f"UID {settings.uid}：{result.msg}{days}"
                        f"{'，奖励 ' + rewards if rewards else ''}（{result.status}，尝试 {attempts} 次）")
    return 0 if result.success else 1


def self_test() -> int:
    lines = []
    try:
        secret = "diagnostic-secret"
        encrypted = autosign.protect(secret)
        if encrypted == secret or autosign.unprotect(encrypted) != secret:
            raise AutoSignError("DPAPI 加解密往返结果不一致")
        lines.append("PASS DPAPI 加解密")
        autosign.build_task_xml("09:00", autosign._current_user_sid(), autosign.task_command())
        lines.append("PASS 当前用户 SID 与任务 XML")
        task = autosign.query_task()
        lines.append(f"PASS 任务计划程序可查询（已安装={task['installed']}，时间={task['time']}）")
        lines.append(f"INFO 解释器：{autosign.task_command()[0]}")
        lines.append(f"INFO 数据目录：{autosign.data_dir()}")
        lines.append("SELF-TEST PASSED")
        code = 0
    except Exception as e:
        lines.append(f"SELF-TEST FAILED: {e}")
        code = 1
    print("\n".join(lines))
    autosign.append_log(" | ".join(lines))
    return code


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(self_test())
    raise SystemExit(asyncio.run(run()))

"""E2B 代码沙箱工具。

让 LLM 可以在隔离的远程沙箱里执行 Python / Shell 代码。

设计原则
────────
1. 按 chat_id 复用：同一群/私聊在 5 分钟空闲窗口内复用同一个沙箱，
   便于连续执行脚本、复用文件系统与已安装依赖；不同 chat 绝不共享沙箱。
2. 串行执行：同一 chat 的沙箱通过锁保护，避免并发命令互相污染。
3. 规格固定：1 vCPU / 512 MB（E2B base 模板默认规格），空闲超时 timeout=E2B_TIMEOUT 秒。
4. 安全降级：未配置 E2B_API_KEY 或 SDK 未安装时，返回友好错误而非抛异常，
   不影响 bot 其它功能。
5. 节制：工具 description 明确提示模型「尽量少用」，避免无意义地频繁拉起沙箱。

注意
────
- 这里保证的是“沙箱实例”按 chat 复用，因此文件系统、安装包、工作目录可复用。
- Python 进程内变量是否跨次保留，取决于 E2B run_code 的内核实现，不作强保证。

对外暴露
────────
- RUN_PYTHON_TOOL_DEFINITION / RUN_SHELL_TOOL_DEFINITION : OpenAI function schema
- E2B_TOOLS : 上述两者组成的 list，供插件注册系统汇总
- execute_run_python(code, chat_id) / execute_run_shell(command, chat_id) : 实际执行协程
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

import app_config.config as config

logger = logging.getLogger("E2BTool")


@dataclass
class _SandboxSession:
    chat_id: str
    sandbox: object
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: float = field(default_factory=time.time)


@dataclass
class _ShipyardSession:
    chat_id: str
    ship_id: str
    session_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: float = field(default_factory=time.time)


_SESSIONS: dict[str, _SandboxSession] = {}
_SESSIONS_GUARD = asyncio.Lock()
_SHIPYARD_SESSIONS: dict[str, _ShipyardSession] = {}
_SHIPYARD_GUARD = asyncio.Lock()

# 单次沙箱操作的硬上限（秒）。
_EXEC_HARD_TIMEOUT = 180

# 单次返回给模型的输出最大字符数（再由 llm_client 按 tool_result_max_chars 二次截断）。
_OUTPUT_MAX_CHARS = 4000

# 沙箱空闲超时：最后一次操作后 600 秒（10 分钟）无后续则自动销毁。
_IDLE_TIMEOUT_S = 600


def _truncate(text: str, limit: int = _OUTPUT_MAX_CHARS) -> str:
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（输出过长，已截断，共 {len(text)} 字符）"


def _e2b_available() -> tuple[bool, str]:
    """检查 E2B 是否可用，返回 (是否可用, 不可用原因)。"""
    if not (getattr(config, "E2B_API_KEY", "") or "").strip():
        return False, "[E2B 未配置：缺少 E2B_API_KEY]"
    try:
        import e2b_code_interpreter  # noqa: F401
    except ImportError:
        return False, "[E2B 不可用：未安装 e2b-code-interpreter，请执行 pip install e2b-code-interpreter]"
    return True, ""


async def _kill_session(session: _SandboxSession) -> None:
    try:
        await session.sandbox.kill()
    except Exception:  # noqa: BLE001
        logger.debug("E2B 沙箱关闭失败（已忽略） | chat=%s", session.chat_id, exc_info=True)


async def _cleanup_expired_sessions() -> None:
    """空闲超过 _IDLE_TIMEOUT_S 的沙箱回收。"""
    now = time.time()
    stale_keys: list[str] = []
    stale_sessions: list[_SandboxSession] = []

    async with _SESSIONS_GUARD:
        for key, sess in list(_SESSIONS.items()):
            if now - sess.last_used_at > _IDLE_TIMEOUT_S and not sess.lock.locked():
                stale_keys.append(key)
                stale_sessions.append(sess)
        for key in stale_keys:
            _SESSIONS.pop(key, None)

    for sess in stale_sessions:
        logger.info("🧹 回收空闲 E2B 沙箱 | chat=%s | idle>=%ss", sess.chat_id, _IDLE_TIMEOUT_S)
        await _kill_session(sess)

    async with _SHIPYARD_GUARD:
        stale_shipyard_keys = [
            key
            for key, sess in _SHIPYARD_SESSIONS.items()
            if now - sess.last_used_at > _IDLE_TIMEOUT_S and not sess.lock.locked()
        ]
        for key in stale_shipyard_keys:
            _SHIPYARD_SESSIONS.pop(key, None)
    if stale_shipyard_keys:
        logger.info("🧹 回收 %s 个空闲 Shipyard session", len(stale_shipyard_keys))


# ── 后台定时清理 ──────────────────────────────────────────
_CLEANUP_INTERVAL_S = 120  # 每 2 分钟扫描一次
_cleanup_task: asyncio.Task | None = None


async def _cleanup_loop():
    """周期性回收空闲沙箱，避免 bot 空闲时沙箱泄漏。"""
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL_S)
            await _cleanup_expired_sessions()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("E2B 定时清理异常（已忽略）", exc_info=True)


def start_cleanup_task():
    """启动后台清理协程（幂等，重复调用安全）。"""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info("🧹 E2B 沙箱定时清理已启动 | interval=%ss idle_timeout=%ss", _CLEANUP_INTERVAL_S, _IDLE_TIMEOUT_S)


async def stop_cleanup_task():
    """停止后台清理协程并销毁所有残留沙箱。"""
    global _cleanup_task
    if _cleanup_task and not _cleanup_task.done():
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass
    _cleanup_task = None
    # 销毁所有残留沙箱
    async with _SESSIONS_GUARD:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for sess in sessions:
        await _kill_session(sess)

    async with _SHIPYARD_GUARD:
        _SHIPYARD_SESSIONS.clear()


async def _get_or_create_session(chat_id: str) -> _SandboxSession:
    ok, reason = _e2b_available()
    if not ok:
        raise RuntimeError(reason)

    from e2b_code_interpreter import AsyncSandbox

    await _cleanup_expired_sessions()

    async with _SESSIONS_GUARD:
        sess = _SESSIONS.get(chat_id)
        if sess is not None:
            sess.last_used_at = time.time()
            return sess

        sandbox = await AsyncSandbox.create(
            api_key=config.E2B_API_KEY,
            timeout=int(getattr(config, "E2B_TIMEOUT", 300)),
        )
        sess = _SandboxSession(chat_id=chat_id, sandbox=sandbox)
        _SESSIONS[chat_id] = sess
        logger.info("🆕 创建 E2B 沙箱 | chat=%s | timeout=%ss", chat_id, int(getattr(config, "E2B_TIMEOUT", 300)))
        return sess


async def _with_sandbox(chat_id: str | int | None, run):
    """按 chat_id 复用沙箱并串行执行 run(sandbox)。

    run 是一个 async 回调，签名 async (sandbox) -> str。
    chat_id 为空时退化到共享默认键，避免工具调用链缺失 chat_id 时直接报错。
    """
    key = str(chat_id) if chat_id is not None else "global"
    try:
        sess = await _get_or_create_session(key)
    except Exception as exc:  # noqa: BLE001
        return str(exc)

    async with sess.lock:
        sess.last_used_at = time.time()
        try:
            result = await asyncio.wait_for(run(sess.sandbox), timeout=_EXEC_HARD_TIMEOUT)
            sess.last_used_at = time.time()
            return result
        except asyncio.TimeoutError:
            async with _SESSIONS_GUARD:
                if _SESSIONS.get(key) is sess:
                    _SESSIONS.pop(key, None)
            await _kill_session(sess)
            logger.warning("E2B 执行超时（>%ss） | chat=%s", _EXEC_HARD_TIMEOUT, key)
            return f"[执行超时：超过 {_EXEC_HARD_TIMEOUT} 秒未完成，已中止]"
        except asyncio.CancelledError:
            async with _SESSIONS_GUARD:
                if _SESSIONS.get(key) is sess:
                    _SESSIONS.pop(key, None)
            await asyncio.shield(_kill_session(sess))
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("E2B 执行异常 | chat=%s", key)
            return f"[E2B 执行失败：{type(exc).__name__}: {str(exc)[:200]}]"

def _sandbox_provider() -> str:
    provider = str(getattr(config, "SANDBOX_PROVIDER", "e2b") or "e2b").strip().lower()
    return provider if provider in ("shipyard", "local") else "e2b"


def _shipyard_available() -> tuple[bool, str]:
    if not (getattr(config, "SHIPYARD_ENDPOINT", "") or "").strip():
        return False, "[Shipyard 未配置：缺少 SHIPYARD_ENDPOINT]"
    if not (getattr(config, "SHIPYARD_ACCESS_TOKEN", "") or "").strip():
        return False, "[Shipyard 未配置：缺少 SHIPYARD_ACCESS_TOKEN]"
    return True, ""


def _shipyard_headers(session_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {getattr(config, 'SHIPYARD_ACCESS_TOKEN', '')}",
        "X-SESSION-ID": session_id,
    }


def _shipyard_spec_payload() -> dict:
    spec: dict[str, object] = {}
    cpus = float(getattr(config, "SHIPYARD_CPUS", 0.0) or 0.0)
    memory = str(getattr(config, "SHIPYARD_MEMORY", "") or "").strip()
    if cpus > 0:
        spec["cpus"] = cpus
    if memory:
        spec["memory"] = memory
    return spec


async def _get_or_create_shipyard_session(chat_id: str) -> _ShipyardSession:
    ok, reason = _shipyard_available()
    if not ok:
        raise RuntimeError(reason)

    await _cleanup_expired_sessions()
    endpoint = str(getattr(config, "SHIPYARD_ENDPOINT", "")).rstrip("/")
    ttl = int(getattr(config, "SHIPYARD_TTL", 3600))
    max_sessions = int(getattr(config, "SHIPYARD_MAX_SESSIONS", 1))
    session_id = f"openmim-{chat_id}"

    async with _SHIPYARD_GUARD:
        sess = _SHIPYARD_SESSIONS.get(chat_id)
        if sess is not None:
            sess.last_used_at = time.time()
            return sess

        payload: dict[str, object] = {"ttl": ttl, "max_session_num": max_sessions}
        spec = _shipyard_spec_payload()
        if spec:
            payload["spec"] = spec

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            resp = await client.post(f"{endpoint}/ship", json=payload, headers=_shipyard_headers(session_id))
            resp.raise_for_status()
            data = resp.json()

        ship_id = str(data.get("id") or "")
        if not ship_id:
            raise RuntimeError(f"[Shipyard 创建 Ship 失败：响应缺少 id: {data}]")
        sess = _ShipyardSession(chat_id=chat_id, ship_id=ship_id, session_id=session_id)
        _SHIPYARD_SESSIONS[chat_id] = sess
        logger.info("🆕 创建 Shipyard Ship | chat=%s | ship=%s | ttl=%ss", chat_id, ship_id, ttl)
        return sess


def _format_shipyard_result(data: dict) -> str:
    if not data.get("success", True):
        return f"error: {data.get('error') or data}"
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        return _truncate(str(data))
    parts: list[str] = []
    output = payload.get("output")
    if isinstance(output, dict):
        text_out = output.get("text")
        if text_out:
            parts.append(str(text_out).rstrip())
        images = output.get("images")
        if images:
            parts.append(f"images: {len(images)} item(s)")
    stdout = payload.get("stdout") or payload.get("text")
    stderr = payload.get("stderr")
    return_code = payload.get("return_code", payload.get("exit_code"))
    if stdout:
        parts.append("stdout:\n" + str(stdout).rstrip())
    if stderr:
        parts.append("stderr:\n" + str(stderr).rstrip())
    if return_code is not None:
        parts.append(f"exit_code: {return_code}")
    error = payload.get("error") or data.get("error")
    if error:
        parts.append("error:\n" + str(error).rstrip())
    if not parts:
        return "[执行完成，无输出]"
    return _truncate("\n\n".join(parts))

async def _shipyard_exec(chat_id: str | int | None, operation_type: str, payload: dict) -> str:
    key = str(chat_id) if chat_id is not None else "global"
    endpoint = str(getattr(config, "SHIPYARD_ENDPOINT", "")).rstrip("/")
    for attempt in range(2):
        try:
            sess = await _get_or_create_shipyard_session(key)
        except Exception as exc:  # noqa: BLE001
            return str(exc)

        async with sess.lock:
            sess.last_used_at = time.time()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(_EXEC_HARD_TIMEOUT, connect=10.0)) as client:
                    resp = await client.post(
                        f"{endpoint}/ship/{sess.ship_id}/exec",
                        json={"type": operation_type, "payload": payload},
                        headers=_shipyard_headers(sess.session_id),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                sess.last_used_at = time.time()
                return _format_shipyard_result(data)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if attempt == 0 and status in (404, 410):
                    async with _SHIPYARD_GUARD:
                        if _SHIPYARD_SESSIONS.get(key) is sess:
                            _SHIPYARD_SESSIONS.pop(key, None)
                    logger.info("Shipyard session 已失效，重建后重试 | chat=%s | status=%s", key, status)
                    continue
                logger.exception("Shipyard 执行异常 | chat=%s | ship=%s", key, sess.ship_id)
                return f"[Shipyard 执行失败：HTTP {status}]"
            except Exception as exc:  # noqa: BLE001
                logger.exception("Shipyard 执行异常 | chat=%s | ship=%s", key, sess.ship_id)
                return f"[Shipyard 执行失败：{type(exc).__name__}: {str(exc)[:200]}]"
    return "[Shipyard 执行失败：session 重建失败]"


def _safe_local_key(chat_id: str | int | None) -> str:
    raw = str(chat_id) if chat_id is not None else "global"
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)
    return safe[:80] or "global"


def _local_workdir(chat_id: str | int | None) -> Path:
    base = Path(getattr(config, "LOCAL_SANDBOX_WORKDIR", "") or Path("data") / "local_sandbox")
    path = base / _safe_local_key(chat_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _run_local_process(argv: list[str], *, input_text: str | None = None, cwd: Path) -> str:
    timeout = int(getattr(config, "LOCAL_SANDBOX_TIMEOUT", _EXEC_HARD_TIMEOUT))
    proc = None
    process_options = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )

    async def _kill_windows_tree(pid: int) -> None:
        await asyncio.to_thread(
            subprocess.run,
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    async def _terminate() -> None:
        if proc is None or proc.returncode is not None:
            return
        try:
            if os.name == "nt":
                await _kill_windows_tree(proc.pid)
                if proc.returncode is None:
                    proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:
            logger.debug("Local sandbox process kill failed", exc_info=True)
        try:
            await proc.communicate()
        except Exception:
            logger.debug("Local sandbox process reap failed", exc_info=True)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE if input_text is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            **process_options,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(input_text.encode() if input_text is not None else None), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate()
        return f"[本地沙箱执行超时：超过 {timeout} 秒未完成，已中止]"
    except asyncio.CancelledError:
        await asyncio.shield(_terminate())
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Local sandbox 执行异常 | cwd=%s", cwd)
        return f"[本地沙箱执行失败：{type(exc).__name__}: {str(exc)[:200]}]"

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    parts: list[str] = []
    if stdout.strip():
        parts.append("stdout:\n" + stdout.rstrip())
    if stderr.strip():
        parts.append("stderr:\n" + stderr.rstrip())
    parts.append(f"exit_code: {proc.returncode}")
    return _truncate("\n\n".join(parts))


async def _local_run_python(code: str, chat_id: str | int | None = None) -> str:
    return await _run_local_process([sys.executable, "-I", "-"], input_text=code, cwd=_local_workdir(chat_id))


async def _local_run_shell(command: str, chat_id: str | int | None = None) -> str:
    return await _run_local_process(["/bin/bash", "-lc", command], cwd=_local_workdir(chat_id))


async def execute_run_python(code: str, chat_id: str | int | None = None) -> str:
    """在沙箱中执行一段 Python 代码，返回 stdout / stderr / 错误信息。"""
    code = (code or "").strip()
    if not code:
        return "[未提供要执行的 Python 代码]"
    if _sandbox_provider() == "local":
        logger.info("🧪 run_python(local) | chat=%s | %s", chat_id, code[:120].replace("\n", " "))
        return await _local_run_python(code, chat_id)
    if _sandbox_provider() == "shipyard":
        logger.info("🧪 run_python(shipyard) | chat=%s | %s", chat_id, code[:120].replace("\n", " "))
        return await _shipyard_exec(
            chat_id,
            "ipython/exec",
            {"code": code, "timeout": int(getattr(config, "SHIPYARD_EXEC_TIMEOUT", _EXEC_HARD_TIMEOUT)), "silent": False},
        )

    async def _run(sandbox) -> str:
        exec_result = await sandbox.run_code(code)
        parts: list[str] = []

        # 标准输出
        stdout = "".join(getattr(exec_result.logs, "stdout", []) or [])
        stderr = "".join(getattr(exec_result.logs, "stderr", []) or [])
        if stdout.strip():
            parts.append("stdout:\n" + stdout.rstrip())
        if stderr.strip():
            parts.append("stderr:\n" + stderr.rstrip())

        # 运行时异常（语法/运行错误）
        err = getattr(exec_result, "error", None)
        if err is not None:
            name = getattr(err, "name", "Error")
            value = getattr(err, "value", "")
            parts.append(f"error: {name}: {value}")

        # 最后一个表达式的求值结果（如 REPL 那样）
        results = getattr(exec_result, "results", None) or []
        text_results = [getattr(r, "text", None) for r in results if getattr(r, "text", None)]
        if text_results:
            parts.append("result:\n" + "\n".join(text_results))

        if not parts:
            return "[执行完成，无输出]"
        return _truncate("\n\n".join(parts))

    logger.info("🧪 run_python | chat=%s | %s", chat_id, code[:120].replace("\n", " "))
    return await _with_sandbox(chat_id, _run)


async def execute_run_shell(command: str, chat_id: str | int | None = None) -> str:
    """在沙箱中执行一条 Shell 命令，返回 stdout / stderr / 退出码。"""
    command = (command or "").strip()
    if not command:
        return "[未提供要执行的 Shell 命令]"
    if _sandbox_provider() == "local":
        logger.info("🐚 run_shell(local) | chat=%s | %s", chat_id, command[:120].replace("\n", " "))
        return await _local_run_shell(command, chat_id)
    if _sandbox_provider() == "shipyard":
        logger.info("🐚 run_shell(shipyard) | chat=%s | %s", chat_id, command[:120].replace("\n", " "))
        return await _shipyard_exec(
            chat_id,
            "shell/exec",
            {"command": command, "timeout": int(getattr(config, "SHIPYARD_EXEC_TIMEOUT", _EXEC_HARD_TIMEOUT)), "shell": True, "background": False},
        )

    async def _run(sandbox) -> str:
        result = await sandbox.commands.run(command, timeout=_EXEC_HARD_TIMEOUT)
        parts: list[str] = []
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        exit_code = getattr(result, "exit_code", None)
        if stdout.strip():
            parts.append("stdout:\n" + stdout.rstrip())
        if stderr.strip():
            parts.append("stderr:\n" + stderr.rstrip())
        if exit_code is not None:
            parts.append(f"exit_code: {exit_code}")
        if not parts:
            return "[命令执行完成，无输出]"
        return _truncate("\n\n".join(parts))

    logger.info("🐚 run_shell | chat=%s | %s", chat_id, command[:120].replace("\n", " "))
    return await _with_sandbox(chat_id, _run)


# ── Tool Definitions（OpenAI function-calling schema）────────────────
# description 中明确提示模型「尽量少用」，引导其仅在真正需要计算/执行时调用。

RUN_PYTHON_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_python",
        "description": (
            "在隔离的远程沙箱中执行一段 Python 代码并返回输出。"
            "适用于：需要精确计算、运行算法、处理数据、验证代码逻辑等无法靠推理直接得出结果的场景。"
            "⚠️ 请尽量少用：拉起沙箱有成本和延迟，普通问答 / 简单心算 / 闲聊都不要调用，"
            "只有当确实需要真实执行代码才用。沙箱无状态，每次调用相互独立，无法保留上一次的变量。"
            "用 print() 输出你想看到的结果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的完整 Python 代码。需要看到的结果请用 print() 输出。",
                }
            },
            "required": ["code"],
        },
    },
}

RUN_SHELL_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": (
            "在隔离的远程沙箱中执行一条 Shell（bash）命令并返回 stdout / stderr / 退出码。"
            "适用于：需要用命令行工具完成的任务（如文件处理、安装临时依赖后再跑脚本等）。"
            "⚠️ 请尽量少用：拉起沙箱有成本和延迟，能用推理或 run_python 解决的就不要用 shell。"
            "沙箱无状态，每次调用相互独立。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的单条 Shell 命令，例如 'pip install numpy && python -c \"print(1)\"'",
                }
            },
            "required": ["command"],
        },
    },
}

E2B_TOOLS = [RUN_PYTHON_TOOL_DEFINITION, RUN_SHELL_TOOL_DEFINITION]

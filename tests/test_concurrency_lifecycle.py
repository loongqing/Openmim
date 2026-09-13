import asyncio
from types import SimpleNamespace

import pytest

import app.main as app_main
import handlers.business_handler as business
import integrations.e2b_tool as sandbox
import integrations.scheduler_tool as scheduler
from stores.context_manager import ContextManager, ContextMessage


@pytest.mark.asyncio
async def test_business_messages_for_same_peer_are_serialized(monkeypatch):
    monkeypatch.setattr(business, "BUSINESS_ENABLED", True)
    business._active_connections.clear()
    business._session_lock._locks.clear()
    business._cache_active_connection("conn", 10)

    active = 0
    max_active = 0
    call_order = []

    async def fake_unlocked(update, context, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        call_order.append(("start", update.business_message.marker))
        await asyncio.sleep(0.01)
        call_order.append(("end", update.business_message.marker))
        active -= 1

    monkeypatch.setattr(business, "_on_business_message_unlocked", fake_unlocked)

    def update(marker):
        user = SimpleNamespace(id=20, is_bot=False)
        msg = SimpleNamespace(
            marker=marker,
            business_connection_id="conn",
            from_user=user,
            sender_chat=None,
        )
        return SimpleNamespace(business_message=msg)

    await asyncio.gather(
        business.on_business_message(update(1), SimpleNamespace(bot=None)),
        business.on_business_message(update(2), SimpleNamespace(bot=None)),
    )

    assert max_active == 1
    assert call_order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]
    assert business._session_lock._locks == {}


@pytest.mark.asyncio
async def test_scheduler_cancel_enforces_chat_ownership():
    scheduler._tasks.clear()
    handle = asyncio.create_task(asyncio.sleep(60))
    task = scheduler.ScheduledTask(
        task_id=99,
        chat_id=123,
        message="private",
        trigger_type="delay",
        handle=handle,
    )
    scheduler._tasks[99] = task

    try:
        result = await scheduler.schedule_task(456, "", action="cancel", task_id=99)
        assert "not found" in result
        assert scheduler._tasks[99] is task
        assert not handle.cancelled()

        result = await scheduler.schedule_task(123, "", action="cancel", task_id=99)
        assert "99" in result
        assert 99 not in scheduler._tasks
        assert handle.cancelled()
    finally:
        if not handle.done():
            handle.cancel()
        await asyncio.gather(handle, return_exceptions=True)
        scheduler._tasks.clear()


@pytest.mark.asyncio
async def test_cancel_all_scheduler_tasks_waits_for_cancellation():
    scheduler._tasks.clear()
    handles = [asyncio.create_task(asyncio.sleep(60)) for _ in range(2)]
    for task_id, handle in enumerate(handles, start=1):
        scheduler._tasks[task_id] = scheduler.ScheduledTask(
            task_id=task_id,
            chat_id=1,
            message="x",
            trigger_type="delay",
            handle=handle,
        )

    await scheduler.cancel_all_tasks()

    assert scheduler._tasks == {}
    assert all(handle.done() and handle.cancelled() for handle in handles)


class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.pid = 12345
        self.killed = False
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.communicate_calls = 0

    async def communicate(self, data=None):
        self.communicate_calls += 1
        self.started.set()
        if self.killed:
            self.returncode = -1
            return b"", b""
        await self.release.wait()
        return b"", b""

    def kill(self):
        self.killed = True
        self.release.set()


@pytest.mark.asyncio
async def test_local_process_is_reaped_when_cancelled(monkeypatch, tmp_path):
    proc = _FakeProcess()
    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", lambda *a, **k: asyncio.sleep(0, result=proc))
    monkeypatch.setattr(
        sandbox.asyncio,
        "to_thread",
        lambda *a, **k: asyncio.sleep(0, result=proc.kill()),
    )

    task = asyncio.create_task(sandbox._run_local_process(["fake"], cwd=tmp_path))
    await proc.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert proc.killed
    assert proc.returncode == -1
    assert proc.communicate_calls >= 2


@pytest.mark.asyncio
async def test_e2b_session_is_evicted_and_killed_on_cancellation(monkeypatch):
    class FakeSandbox:
        def __init__(self):
            self.killed = False

        async def kill(self):
            self.killed = True

    fake = FakeSandbox()
    session = sandbox._SandboxSession(chat_id="7", sandbox=fake)
    sandbox._SESSIONS.clear()
    sandbox._SESSIONS["7"] = session
    monkeypatch.setattr(sandbox, "_get_or_create_session", lambda key: asyncio.sleep(0, result=session))

    started = asyncio.Event()

    async def never_finishes(_):
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(sandbox._with_sandbox(7, never_finishes))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake.killed
    assert "7" not in sandbox._SESSIONS


@pytest.mark.asyncio
async def test_post_shutdown_closes_resources_in_same_coroutine(monkeypatch):
    events = []

    class Manager:
        async def shutdown(self, app_context, application):
            events.append("plugins")

    async def record(name):
        events.append(name)

    monkeypatch.setattr(app_main, "stop_idle_topic_loop", lambda: record("idle"))
    monkeypatch.setattr(app_main, "cancel_all_tasks", lambda: record("scheduler"))
    monkeypatch.setattr(app_main, "stop_cleanup_task", lambda: record("sandbox"))
    monkeypatch.setattr(app_main, "stop_prepare_guess_task", lambda: record("playables"))
    monkeypatch.setattr(app_main, "close_llm_client", lambda: record("llm"))
    monkeypatch.setattr(app_main, "set_application", lambda value: events.append(("application", value)))

    application = SimpleNamespace(
        bot_data={"plugin_manager": Manager(), "app_context": object()}
    )
    await app_main.post_shutdown(application)

    assert events == [
        "idle",
        "scheduler",
        "playables",
        "plugins",
        "sandbox",
        "llm",
        ("application", None),
    ]


@pytest.mark.asyncio
async def test_context_manager_serializes_and_prunes_writes(tmp_path):
    manager = ContextManager(max_messages=3, store_file=str(tmp_path / "context.sqlite3"))

    await asyncio.gather(
        *(manager.append(7, ContextMessage("user", text=str(index))) for index in range(6))
    )
    recent = await manager.get_context(7)

    assert len(recent) == 3
    assert [message.text for message in recent] == ["3", "4", "5"]


@pytest.mark.asyncio
async def test_context_manager_cancellation_waits_for_db_thread_before_releasing_lock(monkeypatch):
    manager = ContextManager()
    thread_started = asyncio.Event()
    release_thread = asyncio.Event()
    second_entered = asyncio.Event()

    async def fake_to_thread(callable_, *args):
        thread_started.set()
        await release_thread.wait()
        return 1

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    first = asyncio.create_task(manager.append(1, ContextMessage("user", text="one")))
    await thread_started.wait()
    first.cancel()

    async def second_append():
        async with manager._lock:
            second_entered.set()

    second = asyncio.create_task(second_append())
    await asyncio.sleep(0)
    assert not second_entered.is_set()

    release_thread.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    assert second_entered.is_set()

"""Finite task-lifecycle controls, not native-runtime qualification."""
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace


def load():
    script = Path(__file__).resolve().parents[1] / 'tools/capacity_service.py'
    spec = importlib.util.spec_from_file_location('capacity_cleanup_fixture', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shutdown_waits_for_lifespan_finally():
    async def scenario():
        server = SimpleNamespace(should_exit=False)
        observed = []
        async def service():
            try:
                while not server.should_exit:
                    await asyncio.sleep(.001)
            finally:
                await asyncio.sleep(.001)
                observed.append('closed')
        task = asyncio.create_task(service())
        await asyncio.sleep(0)
        assert await load().settle_service(server, task, .1) is None
        assert observed == ['closed'] and task.done() and not task.cancelled()
    asyncio.run(scenario())


def test_timeout_retains_live_task_without_cancellation():
    async def scenario():
        release = asyncio.Event()
        server = SimpleNamespace(should_exit=False)
        task = asyncio.create_task(release.wait())
        result = await load().settle_service(server, task, .001)
        assert result['message'] == 'service_lifespan_cleanup_deadline'
        assert server.should_exit and not task.done() and not task.cancelled()
        release.set()
        await task
    asyncio.run(scenario())


def test_completed_failure_is_observed():
    async def scenario():
        async def failed():
            raise RuntimeError('synthetic lifecycle failure')
        task = asyncio.create_task(failed())
        await asyncio.sleep(0)
        result = await load().settle_service(SimpleNamespace(should_exit=False), task)
        assert result == {'type': 'RuntimeError', 'message': 'synthetic lifecycle failure'}
    asyncio.run(scenario())


def test_runner_teardown_cancellation_is_not_graceful_completion():
    observed = []
    async def scenario():
        async def service():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                observed.append('runner_cancelled')
                raise
        task = asyncio.create_task(service())
        result = await load().settle_service(SimpleNamespace(should_exit=False), task, .001)
        assert result['type'] == 'TimeoutError' and not task.done()
        # Exiting the runner cancels leftovers, unlike the helper's grace wait.
    asyncio.run(scenario())
    assert observed == ['runner_cancelled']

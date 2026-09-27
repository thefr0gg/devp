import asyncio
import sys

import pytest

import devp.process as process_module
from devp.config import ProcessConfig
from devp.process import ManagedProcess, ProcessState

pytestmark = pytest.mark.asyncio


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


async def test_start_runs_process_and_captures_output():
    config = ProcessConfig(name="ticker", command=python_command("print('hello')"))
    proc = ManagedProcess(config)

    await proc.start()
    assert proc.state == ProcessState.RUNNING

    await proc._wait_task
    assert proc.state == ProcessState.STOPPED
    assert "hello" in proc.output


async def test_child_does_not_inherit_terminal_stdin():
    config = ProcessConfig(
        name="reader", command=python_command("import sys; print(repr(sys.stdin.read()))")
    )
    proc = ManagedProcess(config)

    await proc.start()
    await asyncio.wait_for(proc._wait_task, timeout=5)
    assert "''" in proc.output


async def test_stop_terminates_running_process():
    config = ProcessConfig(
        name="sleeper", command=python_command("import time; time.sleep(30)")
    )
    proc = ManagedProcess(config)

    await proc.start()
    assert proc.state == ProcessState.RUNNING

    await proc.stop()
    assert proc.state == ProcessState.STOPPED
    assert proc._proc.returncode is not None


async def test_restart_produces_a_new_process():
    config = ProcessConfig(
        name="sleeper", command=python_command("import time; time.sleep(30)")
    )
    proc = ManagedProcess(config)

    await proc.start()
    first_pid = proc._proc.pid

    await proc.restart()
    assert proc.state == ProcessState.RUNNING
    assert proc._proc.pid != first_pid

    await proc.stop()


async def test_nonzero_exit_marks_crashed():
    config = ProcessConfig(name="failer", command=python_command("import sys; sys.exit(1)"))
    proc = ManagedProcess(config)

    await proc.start()
    await proc._wait_task

    assert proc.state == ProcessState.CRASHED


async def test_no_autorestart_by_default():
    config = ProcessConfig(name="failer", command=python_command("import sys; sys.exit(1)"))
    proc = ManagedProcess(config)

    await proc.start()
    await proc._wait_task

    assert proc.state == ProcessState.CRASHED
    assert proc._restart_task is None

    await asyncio.sleep(0.2)
    assert proc.state == ProcessState.CRASHED


async def test_autorestart_respawns_after_crash(monkeypatch):
    monkeypatch.setattr(process_module, "_BASE_RESTART_DELAY", 0.05)
    monkeypatch.setattr(process_module, "_MAX_RESTART_DELAY", 0.05)
    config = ProcessConfig(
        name="failer",
        command=python_command("import sys; sys.exit(1)"),
        autorestart=True,
    )
    proc = ManagedProcess(config)

    await proc.start()
    await proc._wait_task
    assert proc.state == ProcessState.CRASHED
    first_pid = proc._proc.pid
    assert proc._crash_count == 1

    for _ in range(100):
        if proc._crash_count >= 2:
            break
        await asyncio.sleep(0.02)

    assert proc._crash_count == 2
    assert proc._proc.pid != first_pid


async def test_stop_cancels_pending_autorestart(monkeypatch):
    monkeypatch.setattr(process_module, "_BASE_RESTART_DELAY", 1.0)
    config = ProcessConfig(
        name="failer",
        command=python_command("import sys; sys.exit(1)"),
        autorestart=True,
    )
    proc = ManagedProcess(config)

    await proc.start()
    await proc._wait_task
    assert proc.state == ProcessState.CRASHED
    assert proc._restart_task is not None
    first_pid = proc._proc.pid

    await proc.stop()
    assert proc._restart_task is None

    await asyncio.sleep(1.2)
    assert proc._proc.pid == first_pid
    assert proc.state == ProcessState.CRASHED


async def test_output_buffer_respects_maxlen():
    config = ProcessConfig(
        name="chatty",
        command=python_command("[print(i) for i in range(20)]"),
    )
    proc = ManagedProcess(config)
    proc.output = proc.output.__class__(maxlen=5)

    await proc.start()
    await proc._wait_task

    assert len(proc.output) == 5
    assert list(proc.output) == [str(i) for i in range(15, 20)]


@pytest.mark.skipif(
    not process_module._has_console(),
    reason="CTRL_BREAK_EVENT needs a real console attached (none in this host)",
)
async def test_windows_graceful_stop_lets_the_process_clean_up():
    script = (
        "import signal, sys, time\n"
        "def handler(signum, frame):\n"
        "    print('cleanup', flush=True)\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGBREAK, handler)\n"
        "print('ready', flush=True)\n"
        "time.sleep(30)\n"
    )
    config = ProcessConfig(name="graceful", command=python_command(script))
    proc = ManagedProcess(config)

    await proc.start()
    for _ in range(100):
        if "ready" in proc.output:
            break
        await asyncio.sleep(0.05)
    assert "ready" in proc.output

    await proc.stop()

    assert "cleanup" in proc.output
    assert proc.exit_code == 0


@pytest.mark.skipif(
    sys.platform != "win32" or process_module._has_console(),
    reason="only meaningful on Windows without a console attached",
)
async def test_windows_without_console_kills_immediately_without_waiting_out_the_timeout():
    config = ProcessConfig(
        name="sleeper", command=python_command("import time; time.sleep(30)")
    )
    proc = ManagedProcess(config)

    await proc.start()

    loop = asyncio.get_event_loop()
    started = loop.time()
    await proc.stop(timeout=5.0)
    elapsed = loop.time() - started

    assert proc.state == ProcessState.STOPPED
    assert elapsed < 2.0  # should hard-kill immediately, not wait out the timeout


async def test_crash_after_stable_uptime_resets_backoff(monkeypatch):
    monkeypatch.setattr(process_module, "_BASE_RESTART_DELAY", 60.0)
    monkeypatch.setattr(process_module, "STABLE_UPTIME", 0.3)
    config = ProcessConfig(
        name="flaky",
        command=python_command("import sys, time; time.sleep(0.5); sys.exit(1)"),
        autorestart=True,
    )
    proc = ManagedProcess(config)
    proc._crash_count = 5  # pretend it has been crash-looping

    await proc.start()
    await proc._wait_task
    assert proc._crash_count == 1  # it ran longer than STABLE_UPTIME: backoff starts over
    await proc.stop()


async def test_quick_crashes_keep_escalating_backoff(monkeypatch):
    monkeypatch.setattr(process_module, "_BASE_RESTART_DELAY", 60.0)
    monkeypatch.setattr(process_module, "STABLE_UPTIME", 30.0)
    config = ProcessConfig(
        name="crashloop",
        command=python_command("import sys; sys.exit(1)"),
        autorestart=True,
    )
    proc = ManagedProcess(config)
    proc._crash_count = 5

    await proc.start()
    await proc._wait_task
    assert proc._crash_count == 6
    await proc.stop()


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_ready_when_holds_starting_until_the_pattern_is_printed():
    config = ProcessConfig(
        name="api",
        command=python_command(
            "import time; print('booting'); time.sleep(0.4); "
            "print('\\x1b[32mlistening on :8000\\x1b[0m'); time.sleep(30)"
        ),
        ready_when=r"listening on :\d+",
    )
    proc = ManagedProcess(config)
    await proc.start()
    assert proc.state == ProcessState.STARTING

    assert await asyncio.wait_for(proc.wait_ready(), 5) is True
    assert proc.state == ProcessState.RUNNING
    await proc.stop()


async def test_ready_port_waits_for_a_listening_socket():
    port = free_port()
    config = ProcessConfig(
        name="db",
        command=python_command(
            "import socket, time; time.sleep(0.4); s = socket.socket(); "
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); "
            f"s.bind(('127.0.0.1', {port})); s.listen(); time.sleep(30)"
        ),
        ready_port=port,
    )
    proc = ManagedProcess(config)
    await proc.start()
    assert proc.state == ProcessState.STARTING

    assert await asyncio.wait_for(proc.wait_ready(), 5) is True
    assert proc.state == ProcessState.RUNNING
    await proc.stop()


async def test_exiting_before_ready_is_not_ready():
    config = ProcessConfig(
        name="api", command=python_command("import sys; sys.exit(3)"), ready_when="never"
    )
    proc = ManagedProcess(config)
    await proc.start()
    assert await asyncio.wait_for(proc.wait_ready(), 5) is False
    await proc.wait()
    assert proc.state == ProcessState.CRASHED


async def test_ready_timeout_reports_and_leaves_process_running():
    errors = []
    config = ProcessConfig(
        name="api",
        command=python_command("import time; time.sleep(30)"),
        ready_when="never printed",
        ready_timeout=0.3,
    )
    proc = ManagedProcess(config, on_error=lambda name, text: errors.append(text))
    await proc.start()

    assert await asyncio.wait_for(proc.wait_ready(), 5) is False
    assert proc.state == ProcessState.RUNNING
    assert errors == ["no ready signal after 0.3s"]
    assert "--- no ready signal after 0.3s ---" in proc.output
    await proc.stop()


async def test_stop_while_starting():
    config = ProcessConfig(
        name="api", command=python_command("import time; time.sleep(30)"), ready_when="never"
    )
    proc = ManagedProcess(config)
    await proc.start()
    assert proc.state == ProcessState.STARTING
    await proc.stop()
    assert proc.state == ProcessState.STOPPED
    assert await proc.wait_ready() is False


async def test_without_a_ready_check_the_process_is_ready_immediately():
    config = ProcessConfig(name="api", command=python_command("import time; time.sleep(30)"))
    proc = ManagedProcess(config)
    await proc.start()
    assert proc.state == ProcessState.RUNNING
    assert await proc.wait_ready() is True
    await proc.stop()


async def collect(code: str) -> list[str]:
    proc = ManagedProcess(ProcessConfig(name="p", command=python_command(code)))
    await proc.start()
    await proc.wait()
    await proc._pump_task
    return list(proc.output)


async def test_progress_redraws_collapse_to_the_final_frame():
    code = (
        "import sys; out = sys.stdout.buffer; "
        "out.write('\\ufeffbuilding [  4%] a\\rbuilding [ 50%] b\\rbuilding [100%] c\\n'.encode()); "
        "out.write(b'\\x1b[2K\\x1b[1Gspin 1\\x1b[2K\\x1b[1Gspin 2\\n'); "
        "out.write(b'abc\\x08\\x08XY\\r\\n')"
    )
    assert await collect(code) == ["building [100%] c", "spin 2", "aXY"]


async def test_a_line_longer_than_the_read_buffer_does_not_stop_output():
    code = "print('x' * 200_000); print('still streaming')"
    output = await collect(code)
    assert output[-1] == "still streaming"
    assert "".join(output[:-1]) == "x" * 200_000


async def test_emoji_and_unicode_output_is_preserved():
    line = "🚀 ✅ ⚠️ 👨‍👩‍👧 日本語 ┌─┐ ⠋ é"
    code = f"import sys; sys.stdout.buffer.write({line!r}.encode() + b'\\n')"
    assert await collect(code) == [line]


async def test_python_children_are_asked_for_utf8_output(monkeypatch):
    # On Windows a piped Python child otherwise writes in the console's legacy code
    # page and crashes on the first emoji it prints.
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    code = "import os, sys; print(os.environ['PYTHONIOENCODING'], sys.stdout.encoding)"
    assert await collect(code) == ["utf-8 utf-8"]


async def test_a_users_own_pythonioencoding_is_respected():
    proc = ManagedProcess(
        ProcessConfig(
            name="p",
            command=python_command("import os; print(os.environ['PYTHONIOENCODING'])"),
            env={"PYTHONIOENCODING": "utf-8:backslashreplace"},
        )
    )
    await proc.start()
    await proc.wait()
    await proc._pump_task
    assert list(proc.output) == ["utf-8:backslashreplace"]


async def test_non_utf8_output_uses_the_fallback_encoding(monkeypatch):
    monkeypatch.setattr(process_module, "_FALLBACK_ENCODING", "cp1252")
    output = await collect("import sys; sys.stdout.buffer.write('café\\n'.encode('cp1252'))")
    assert output == ["café"]

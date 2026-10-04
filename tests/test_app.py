import asyncio
import re
import sys

import pytest
from textual.widgets import Input, ListView

from devp.app import DevpApp
from devp.config import Config, ProcessConfig
from devp.manager import ProcessManager
from devp.process import ProcessState

pytestmark = pytest.mark.asyncio


def python_command(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", code]


def make_app(**process_kwargs) -> DevpApp:
    config = Config(
        processes=[
            ProcessConfig(
                name="sleeper",
                command=python_command("import time; time.sleep(30)"),
                **process_kwargs,
            )
        ],
        crons=[],
    )
    return DevpApp(ProcessManager(config))


async def _wait_for(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def test_escape_closes_search_restores_focus_and_clears_text():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar", ListView)
        search_input = app.query_one("#search-input", Input)

        await pilot.press("slash")
        await pilot.pause()
        assert search_input.has_focus

        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("escape")
        await pilot.pause()

        assert search_input.display is False
        assert search_input.value == ""
        assert sidebar.has_focus is True
        assert search_input.has_focus is False

        await app.manager.shutdown_all()


async def test_keybindings_work_again_after_closing_search():
    """Regression test: a stale-focused hidden search input must not swallow keystrokes."""
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]

        await pilot.press("slash")
        await pilot.press("x")  # typed into the search box, must NOT stop the process
        await pilot.press("escape")
        await pilot.pause()
        assert sleeper.state == ProcessState.RUNNING

        await pilot.press("x")  # now that search is closed, this should reach the binding
        await asyncio.sleep(0.3)
        assert sleeper.state == ProcessState.STOPPED


async def test_submitting_search_clears_value_and_restores_focus():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#log")
        search_input = app.query_one("#search-input", Input)

        await pilot.press("slash")
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()

        assert search_input.display is False
        assert search_input.value == ""
        assert log.has_focus is True

        await app.manager.shutdown_all()


async def test_escape_is_a_noop_when_search_is_not_open():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        log = app.query_one("#log")
        log.focus()
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert log.has_focus is True  # escape must not steal focus when search is closed

        await app.manager.shutdown_all()


async def test_focused_pane_border_is_highlighted():
    app = make_app()
    async with app.run_test() as pilot:
        sidebar = app.query_one("#sidebar")
        log_pane = app.query_one("#log-pane")
        await pilot.pause()
        assert sidebar.styles.border_top[1] != log_pane.styles.border_top[1]
        sidebar_focused_color = sidebar.styles.border_top[1]

        app.query_one("#log").focus()
        await pilot.pause()
        assert log_pane.styles.border_top[1] == sidebar_focused_color
        assert sidebar.styles.border_top[1] != sidebar_focused_color
        assert log_pane.border_title.startswith("Logs · sleeper")


async def test_search_highlights_and_navigates_matches():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        for i in range(30):
            sleeper.log(f"{'needle' if i % 10 == 0 else 'hay'} {i}")
        await asyncio.sleep(0.1)
        log = app.query_one("#log")
        assert log.line_count == 30

        await pilot.press("slash")
        for ch in "needle":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        matches = log.find("needle")
        assert len(matches) == 3
        assert app._search_cursor == matches[0]

        await pilot.press("n")
        assert app._search_cursor == matches[1]
        await pilot.press("N", "N")
        assert app._search_cursor == matches[2]  # wrapped around

        await app.manager.shutdown_all()


async def test_output_of_unselected_processes_is_not_written_to_the_log():
    config = Config(
        processes=[
            ProcessConfig(name="a", command=python_command("import time; time.sleep(30)")),
            ProcessConfig(name="b", command=python_command("import time; time.sleep(30)")),
        ],
        crons=[],
    )
    app = DevpApp(ProcessManager(config))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.manager.processes["b"].log("from b")
        app.manager.processes["a"].log("from a")
        await asyncio.sleep(0.1)
        log = app.query_one("#log")
        assert log.find("from") == [log.end_index - 1]

        await pilot.press("down")  # select 'b': its buffered output is replayed
        await pilot.pause()
        assert log.line_count == 1 and log.find("from b")

        await app.manager.shutdown_all()


async def test_running_process_glyph_animates():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        item = app._list_items["sleeper"]
        seen = set()
        for _ in range(10):  # a full twinkle cycle is 0.8s
            await asyncio.sleep(0.1)
            seen.add(item.label_text)
        assert len(seen) > 2

        await app.manager.shutdown_all()


async def test_toasts_that_do_not_fit_the_window_are_dropped_oldest_first():
    app = make_app()
    async with app.run_test(size=(80, 16), notifications=True) as pilot:
        await pilot.pause()
        app.clear_notifications()
        for i in range(10):
            app.notify(f"note {i}", timeout=30)
        await pilot.pause()
        messages = [n.message for n in app._notifications]
        assert 1 <= len(messages) < 10
        assert messages[-1] == "note 9"
        for toast in app.screen.query("Toast"):
            assert toast.region.y >= 1  # nothing pushed above the top of the log pane

        await app.manager.shutdown_all()


async def test_toasts_move_above_the_search_bar_while_it_is_open():
    app = make_app()
    async with app.run_test(notifications=True) as pilot:
        await pilot.pause()
        rack = app.screen.query_one("ToastRack")
        assert rack.styles.margin.bottom == 2
        await pilot.press("slash")
        await pilot.pause()
        assert rack.styles.margin.bottom == 5
        await pilot.press("escape")
        await pilot.pause()
        assert rack.styles.margin.bottom == 2

        await app.manager.shutdown_all()


async def test_double_clicking_a_stopped_process_starts_it():
    app = make_app(autostart=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        assert sleeper.state == ProcessState.STOPPED

        await pilot.click(app._list_items["sleeper"], times=2)
        await asyncio.sleep(0.3)
        assert sleeper.state == ProcessState.RUNNING

        await app.manager.shutdown_all()


async def test_single_click_does_not_start_a_process():
    app = make_app(autostart=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(app._list_items["sleeper"])
        await asyncio.sleep(0.3)
        assert app.manager.processes["sleeper"].state == ProcessState.STOPPED


async def test_ctrl_c_copies_selected_log_text_instead_of_quitting(monkeypatch):
    from textual.geometry import Offset
    from textual.selection import Selection

    monkeypatch.setattr("devp.app.copy_native", lambda text: True)
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.manager.processes["sleeper"].log("hello world")
        await asyncio.sleep(0.1)
        log = app.query_one("#log")
        app.screen.selections = {log: Selection(Offset(0, 0), Offset(5, 0))}
        await pilot.pause()

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.clipboard == "hello"
        assert app.is_running
        assert not app.screen.selections

        await app.manager.shutdown_all()


async def test_ctrl_c_quits_when_nothing_is_selected():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await asyncio.sleep(0.5)
        assert not app.is_running
        assert app.manager.processes["sleeper"].state == ProcessState.STOPPED


async def test_uses_the_rose_pine_theme():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme == "rose-pine"
        await app.manager.shutdown_all()


async def test_ui_stays_responsive_while_waiting_for_a_dependency_to_be_ready():
    config = Config(
        processes=[
            ProcessConfig(
                name="db",
                command=python_command("import time; time.sleep(3); print('up'); time.sleep(30)"),
                ready_when="up",
            ),
            ProcessConfig(
                name="api",
                command=python_command("import time; time.sleep(30)"),
                depends_on=["db"],
            ),
        ],
        crons=[],
    )
    app = DevpApp(ProcessManager(config))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.manager.processes["db"].state == ProcessState.STARTING

        await pilot.press("down")  # handled right away, not after db becomes ready
        await pilot.pause()
        assert app.selected_name == "api"
        assert app.manager.processes["db"].state == ProcessState.STARTING

        await _wait_for(lambda: app.manager.processes["api"].state == ProcessState.RUNNING, 10)

        await app.manager.shutdown_all()


async def test_terminal_colors_match_theme_while_running(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(
        "textual.drivers.headless_driver.HeadlessDriver.write",
        lambda self, data: written.append(data),
    )
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sent = "".join(written)
        assert "\x1b]11;rgb:19/17/24\x07" in sent  # Rosé Pine base
        assert "\x1b]10;rgb:e0/de/f4\x07" in sent  # Rosé Pine text
        await app.manager.shutdown_all()
    assert written[-1] == "\x1b]111\x07\x1b]110\x07"  # restored on exit


async def test_base_background_is_the_terminal_default():
    """No painted background: it would show as a frame against the terminal's padding."""
    app = make_app()
    async with app.run_test(size=(60, 12)) as pilot:
        await pilot.pause()
        app.query_one("#log").focus()  # sidebar row highlight is the only painted cell run
        await pilot.pause()
        cells = [
            seg.style.bgcolor
            for strip in app.screen._compositor.render_strips()
            for seg in strip
            if seg.text.strip() == "" and seg.style is not None
        ]
        painted = [bg for bg in cells if bg is not None and not bg.is_default]
        assert len(painted) <= 30  # just the (unfocused) selected sidebar row
        await app.manager.shutdown_all()


def two_process_app() -> DevpApp:
    config = Config(
        processes=[
            ProcessConfig(name="db", command=python_command("import time; time.sleep(30)")),
            ProcessConfig(
                name="api",
                command=python_command("import time; time.sleep(30)"),
                depends_on=["db"],
                autostart=False,
            ),
        ],
        crons=[],
    )
    return DevpApp(ProcessManager(config))


async def test_start_all_and_stop_all():
    app = two_process_app()
    async with app.run_test() as pilot:
        db, api = app.manager.processes["db"], app.manager.processes["api"]
        await _wait_for(lambda: db.state == ProcessState.RUNNING)
        assert api.state == ProcessState.STOPPED  # autostart = false

        await pilot.press("S")
        await _wait_for(lambda: api.state == ProcessState.RUNNING)

        await pilot.press("X")
        await _wait_for(
            lambda: db.state == ProcessState.STOPPED and api.state == ProcessState.STOPPED
        )
        await app.manager.shutdown_all()


async def test_clear_log_empties_the_selected_process_buffer():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        sleeper.log("old line")
        log = app.query_one("#log")
        await _wait_for(lambda: log.line_count == 1)

        log.focus()  # c belongs to the log pane
        await pilot.press("c")
        await pilot.pause()
        assert log.line_count == 0 and len(sleeper.output) == 0

        sleeper.log("new line")  # later output still streams in
        await _wait_for(lambda: log.find("new line"))
        await app.manager.shutdown_all()


async def test_log_title_shows_live_process_details():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        log_pane = app.query_one("#log-pane")
        await _wait_for(lambda: f"pid {sleeper.pid}" in str(log_pane.border_title))
        assert re.search(r"up \d+s", log_pane.border_title)
        assert "restarts" not in log_pane.border_title

        await sleeper.restart()
        await _wait_for(lambda: "restarts 1" in str(log_pane.border_title))

        await sleeper.stop()
        # "pid" goes as soon as the stop begins; the exit code arrives a moment later.
        await _wait_for(lambda: "exit" in str(log_pane.border_title))
        assert "pid" not in log_pane.border_title
        await app.manager.shutdown_all()


async def test_more_lines_indicator_and_follow():
    app = make_app()
    async with app.run_test(size=(80, 16)) as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        for i in range(100):
            sleeper.log(f"line {i}")
        log = app.query_one("#log")
        log_pane = app.query_one("#log-pane")
        await _wait_for(lambda: log.line_count == 100)
        await pilot.pause()
        assert log.lines_below == 0 and not log_pane.border_subtitle  # following the tail

        log.scroll_home(animate=False)
        await _wait_for(lambda: "more lines" in str(log_pane.border_subtitle))
        assert f"▼ {log.lines_below} more lines" in log_pane.border_subtitle

        sleeper.log("new while scrolled up")  # doesn't yank the view back down
        await pilot.pause()
        assert log.scroll_y == 0

        log.focus()  # G belongs to the log pane
        await pilot.press("G")
        await _wait_for(lambda: not log_pane.border_subtitle)
        assert log.is_vertical_scroll_end

        sleeper.log("followed again")  # following resumes
        await _wait_for(lambda: log.find("followed again"))
        await pilot.pause()
        assert log.is_vertical_scroll_end
        await app.manager.shutdown_all()


async def test_help_screen_opens_and_closes():
    from devp.screens import HelpScreen

    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpScreen)
        await app.manager.shutdown_all()


def footer_keys(app) -> set[str]:
    """The keys the footer shows right now (enabled, visible bindings)."""
    return {
        key
        for key, active in app.screen.active_bindings.items()
        if active.binding.show and active.enabled
    }


async def test_footer_shows_only_the_keys_that_work_where_focus_is():
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        processes = {"s", "x", "r", "S", "X", "slash", "question_mark", "q"}
        assert footer_keys(app) == processes

        app.query_one("#log").focus()
        await pilot.pause()
        assert footer_keys(app) == {"slash", "G", "c", "question_mark", "q"}

        await pilot.press("slash")
        await pilot.pause()
        assert footer_keys(app) == {"enter", "escape"}  # typing into the search bar
        assert app.screen.active_bindings["escape"].binding.description == "Cancel"

        await pilot.press(*"zzz", "enter")  # run a search; focus returns to the log
        await pilot.pause()
        assert footer_keys(app) == {"slash", "G", "c", "n", "N", "escape", "question_mark", "q"}
        assert app.screen.active_bindings["escape"].binding.description == "End search"

        await pilot.press("escape")  # clear the search: n / N / Esc go away again
        await pilot.pause()
        assert footer_keys(app) == {"slash", "G", "c", "question_mark", "q"}

        app.query_one("#sidebar").focus()
        await pilot.pause()
        assert footer_keys(app) == processes
        await app.manager.shutdown_all()


async def test_process_keys_do_nothing_outside_the_process_list():
    app = make_app(autostart=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        sleeper = app.manager.processes["sleeper"]
        app.query_one("#log").focus()
        await pilot.pause()
        await pilot.press("s")
        await asyncio.sleep(0.3)
        assert sleeper.state == ProcessState.STOPPED

        app.query_one("#sidebar").focus()
        await pilot.pause()
        await pilot.press("s")
        await _wait_for(lambda: sleeper.state == ProcessState.RUNNING)
        await app.manager.shutdown_all()


async def test_config_version_warnings_are_shown_as_toasts():
    config = Config(
        processes=[ProcessConfig(name="api", command=python_command("pass"), autostart=False)],
        crons=[],
        warnings=["devp.toml doesn't record a config-version"],
    )
    app = DevpApp(ProcessManager(config))
    async with app.run_test(notifications=True) as pilot:
        await pilot.pause()
        [toast] = [n for n in app._notifications if n.title == "Config version"]
        assert toast.severity == "warning" and "config-version" in toast.message


async def test_exiting_without_action_quit_still_stops_running_processes():
    app = make_app()
    sleeper = app.manager.processes["sleeper"]
    async with app.run_test() as pilot:
        await sleeper.start()
        await _wait_for(lambda: sleeper.state == ProcessState.RUNNING)
        app.exit()  # e.g. SIGTERM or a closed terminal: bypasses action_quit
        await pilot.pause()
    assert sleeper.state == ProcessState.STOPPED

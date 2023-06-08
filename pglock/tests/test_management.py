import io
import threading
import time

from django.core.management import call_command
from django.db import transaction
from django.db.utils import OperationalError
import pgactivity
import pytest

import pglock
from pglock.management.commands import pglock as pglock_command


@pytest.fixture(autouse=True)
def patch_get_terminal_width(mocker):
    mocker.patch(
        "pglock.management.commands.pglock.get_terminal_width",
        autospec=True,
        return_value=80,
    )


@pytest.mark.django_db
def test_listing(capsys, reraise, mocker):
    mocker.patch(
        "pglock.management.commands.pglock._handle_user_input",
        autospec=True,
        side_effect=[False, True],
    )

    call_command("pglock")
    captured = capsys.readouterr()
    assert len(captured.out.split("\n")) >= 2

    call_command("pglock", "-e")
    captured = capsys.readouterr()
    assert len(captured.out.split("\n")) >= 10

    call_command("pglock", "-c", "bad-pid")
    captured = capsys.readouterr()
    assert len(captured.out.split("\n")) == 1

    barrier3 = threading.Barrier(3)
    barrier2 = threading.Barrier(2)
    blocked_pid = []
    blocking_pid = []

    @reraise.wrap
    def assert_lock():
        barrier3.wait(timeout=5)

        call_command("pglock", blocking_pid[0])
        captured = capsys.readouterr()
        assert len(captured.out.split("\n")) == 2

        # There's a small possibility of a race condition here
        # that we have no control over. Sleep to reduce the chances
        time.sleep(0.25)
        call_command("pglock", blocked_pid[0], "-f", "granted=False")
        captured = capsys.readouterr()
        assert len(captured.out.split("\n")) == 2

        call_command("pglock", "--on", "auth.User")
        captured = capsys.readouterr()
        assert len(captured.out.split("\n")) == 3

        call_command("pglock", blocked_pid[0], "--blocking")
        captured = capsys.readouterr()
        assert len(captured.out.split("\n")) == 2

        with pytest.raises(SystemExit):
            call_command("pglock", blocked_pid[0], "--blocking", "--terminate")

        call_command("pglock", blocked_pid[0], "--blocking", "--terminate", "--yes")
        captured = capsys.readouterr()
        assert "Terminated" in captured.out

        barrier2.wait(timeout=5)
        barrier3.wait(timeout=5)

    @reraise.wrap
    def lock_1():
        blocking_pid.append(pgactivity.pid())

        with pytest.raises(OperationalError, match="terminat"):
            with transaction.atomic():
                pglock.model("auth.User")
                barrier3.wait(timeout=5)
                barrier2.wait(timeout=5)

        barrier3.wait(timeout=5)

    @reraise.wrap
    def lock_2():
        blocked_pid.append(pgactivity.pid())
        with transaction.atomic():
            barrier3.wait(timeout=5)
            pglock.model("auth.User")

        barrier3.wait(timeout=5)

    assert_lock_thread = threading.Thread(target=assert_lock)
    lock_1_thread = threading.Thread(target=lock_1)
    lock_2_thread = threading.Thread(target=lock_2)
    assert_lock_thread.start()
    lock_1_thread.start()
    lock_2_thread.start()
    assert_lock_thread.join()
    lock_1_thread.join()
    lock_2_thread.join()


def test_handle_user_input(mocker):
    mocker.patch("builtins.input", lambda *args: "y")
    stdout = io.StringIO()
    assert pglock_command._handle_user_input(cfg={}, num_locks=1, stdout=stdout)
    assert not stdout.getvalue()

    mocker.patch("builtins.input", lambda *args: "n")
    stdout = io.StringIO()
    assert not pglock_command._handle_user_input(cfg={}, num_locks=1, stdout=stdout)
    assert stdout.getvalue() == "Aborting!"

    stdout = io.StringIO()
    assert pglock_command._handle_user_input(cfg={"yes": True}, num_locks=1, stdout=stdout)
    assert not stdout.getvalue()

    stdout = io.StringIO()
    assert not pglock_command._handle_user_input(cfg={}, num_locks=0, stdout=stdout)
    assert stdout.getvalue() == "No queries to terminate."

    stdout = io.StringIO()
    assert not pglock_command._handle_user_input(cfg={"cancel": True}, num_locks=0, stdout=stdout)
    assert stdout.getvalue() == "No queries to cancel."

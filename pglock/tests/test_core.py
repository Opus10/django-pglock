import datetime as dt
import random
import threading

import ddf
from django.contrib.auth.models import Group, User
from django.db import connection, transaction
from django.db.utils import IntegrityError, OperationalError
import pytest

import pglock


@pytest.mark.django_db(transaction=True)
def test_timeout():
    ddf.G("auth.User", username="hello")

    def get_timeout():
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            return cursor.fetchone()[0]

    with pglock.timeout(1):
        assert get_timeout() == "1s"

        with pglock.timeout(seconds=2):
            assert get_timeout() == "2s"

            try:
                with transaction.atomic():  # pragma: no branch
                    with pglock.timeout(seconds=3):
                        assert get_timeout() == "3s"

                    assert get_timeout() == "2s"

                    with pglock.timeout(seconds=4):
                        assert get_timeout() == "4s"

                        try:
                            with transaction.atomic():  # pragma: no branch
                                with pglock.timeout(seconds=5):
                                    assert get_timeout() == "5s"
                                    User.objects.create(username="hello")
                        except IntegrityError:
                            pass

                        assert get_timeout() == "4s"

                    with pglock.timeout(None):
                        assert get_timeout() == "0"

                    with pglock.timeout(seconds=6):
                        User.objects.create(username="hello")
            except IntegrityError:
                pass

            assert get_timeout() == "2s"

        assert get_timeout() == "1s"


def test_timeout_args():
    with pytest.raises(ValueError, match="Must supply a value"):
        with pglock.timeout():
            pass

    with pytest.raises(TypeError, match="Must supply int"):
        with pglock.timeout("1"):
            pass

    with pytest.raises(ValueError, match="Must supply value greater"):
        with pglock.timeout(0):
            pass

    with pytest.raises(ValueError, match="Must supply value greater"):
        with pglock.timeout(microseconds=1):
            pass


def test_advisory_args():
    with pytest.raises(ValueError, match="supply a lock ID"):
        with pglock.advisory(None):
            pass

    with pytest.raises(TypeError):
        with pglock.advisory(dt.timedelta(seconds=100)):
            pass

    with pytest.raises(ValueError, match="side_effect must be"):
        with pglock.advisory("lock_id", side_effect="bad"):
            pass

    with pytest.raises(ValueError, match="Cannot use pglock.Return"):

        @pglock.advisory("lock_id", side_effect=pglock.Return)
        def hello():
            raise AssertionError

        hello()

    with pytest.raises(ValueError, match="Skip in a context manager"):
        with pglock.advisory("lock_id", side_effect=pglock.Skip):
            pass

    with pytest.raises(TypeError, match="Must supply int"):
        with pglock.advisory("lock_id", timeout="1"):
            pass


@pytest.mark.django_db()
def test_advisory_no_wait(reraise):
    barrier = threading.Barrier(2)
    rand_val = str(random.random())

    @reraise.wrap
    def assert_lock_not_acquired():
        barrier.wait(timeout=5)

        with pglock.advisory(rand_val, timeout=0) as acquired:
            assert not acquired

        with pytest.raises(OperationalError):
            with pglock.advisory(rand_val, timeout=0, side_effect=pglock.Raise):
                pass

        barrier.wait(timeout=5)

    @reraise.wrap
    def assert_lock_acquired():
        with pglock.advisory(rand_val, timeout=0) as acquired:
            assert acquired
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    not_acquired = threading.Thread(target=assert_lock_not_acquired)
    acquired = threading.Thread(target=assert_lock_acquired)
    not_acquired.start()
    acquired.start()
    not_acquired.join()
    acquired.join()


@pytest.mark.django_db()
def test_advisory_decorator(reraise):
    barrier = threading.Barrier(2)
    rand_val = str(random.random())

    @pglock.advisory(rand_val, timeout=0)
    def decorated_default():
        raise AssertionError

    @pglock.advisory(rand_val, timeout=0, side_effect=pglock.Raise)
    def decorated_raise():
        pass

    @pglock.advisory(rand_val, timeout=0, side_effect=pglock.Skip)
    def decorated_skip():
        raise AssertionError

    @pglock.advisory(timeout=0)
    def can_acquire():
        return True

    @reraise.wrap
    def assert_lock_not_acquired():
        barrier.wait(timeout=5)

        with pytest.raises(OperationalError):
            decorated_default()

        with pytest.raises(OperationalError):
            decorated_raise()

        assert decorated_skip() is None

        barrier.wait(timeout=5)

        assert can_acquire()

    @reraise.wrap
    def assert_lock_acquired():
        with pglock.advisory(rand_val, timeout=0) as acquired:
            assert acquired
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    not_acquired = threading.Thread(target=assert_lock_not_acquired)
    acquired = threading.Thread(target=assert_lock_acquired)
    not_acquired.start()
    acquired.start()
    not_acquired.join()
    acquired.join()


@pytest.mark.django_db()
def test_advisory_timeout(reraise):
    barrier = threading.Barrier(2)
    rand_val = random.randint(0, 100000)

    @pglock.advisory(rand_val, side_effect=pglock.Skip, timeout=pglock.timedelta(milliseconds=100))
    def decorated():
        raise AssertionError  # This wont be called

    @reraise.wrap
    def assert_lock_timeout():
        barrier.wait(timeout=5)

        with pglock.advisory(rand_val, timeout=0.1) as acquired:
            assert not acquired

        with transaction.atomic():
            with pglock.advisory(rand_val, timeout=pglock.timedelta(milliseconds=100)) as acquired:
                assert not acquired

            # Verify the transaction isn't in an errored state
            assert not User.objects.exists()

        with transaction.atomic():
            decorated()

            # Verify the transaction isn't in an errored state
            assert not User.objects.exists()

        with pytest.raises(OperationalError):
            with pglock.advisory(
                rand_val, timeout=pglock.timedelta(milliseconds=100), side_effect=pglock.Raise
            ):
                pass

        barrier.wait(timeout=5)

    @reraise.wrap
    def assert_lock_acquired():
        with pglock.advisory(rand_val, timeout=0) as acquired:
            assert acquired
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    timed_out = threading.Thread(target=assert_lock_timeout)
    acquired = threading.Thread(target=assert_lock_acquired)
    timed_out.start()
    acquired.start()
    timed_out.join()
    acquired.join()


@pytest.mark.django_db(transaction=True)
def test_advisory_transaction(reraise):
    """Test errored transaction behavior for advisory locks"""
    barrier = threading.Barrier(2)
    rand_val = str(random.random())
    ddf.G("auth.User", username="hello")

    @reraise.wrap
    def assert_lock_acquired():
        barrier.wait(timeout=5)

        with pglock.advisory(rand_val) as acquired:
            assert acquired

        barrier.wait(timeout=5)

    @reraise.wrap
    def hold_lock_and_error():
        try:
            with transaction.atomic():  # pragma: no branch
                with pglock.advisory(rand_val) as acquired:
                    assert acquired
                    barrier.wait(timeout=5)

                    # Create a transaction error. The other
                    # thread should be able to acquire the lock
                    User.objects.create(username="hello")
        except IntegrityError:
            pass

        barrier.wait(timeout=5)

    hold_lock = threading.Thread(target=hold_lock_and_error)
    acquired = threading.Thread(target=assert_lock_acquired)
    hold_lock.start()
    acquired.start()
    hold_lock.join()
    acquired.join()


def test_advsiory_id():
    assert pglock.advisory_id(9223372036854775807) == (2147483647, 4294967295)
    assert pglock.advisory_id("hello") == (245608543, 3125670444)


def test_model_args():
    with pytest.raises(ValueError):
        pglock.model(side_effect="bad")

    with pytest.raises(ValueError, match="Must supply at least"):
        pglock.model()

    with pytest.raises(TypeError, match="Must supply int"):
        pglock.model("auth.User", timeout="1")

    with pytest.raises(RuntimeError, match="transaction"):
        pglock.model("auth.User")


@pytest.mark.django_db()
def test_model_no_wait(reraise):
    barrier = threading.Barrier(2)

    @reraise.wrap
    def assert_lock_not_acquired():
        barrier.wait(timeout=5)
        with transaction.atomic():
            assert not pglock.model("auth.User", timeout=0)

        with transaction.atomic():
            with pytest.raises(OperationalError):
                pglock.model("auth.User", timeout=0, side_effect=pglock.Raise)

        barrier.wait(timeout=5)

    @reraise.wrap
    def assert_lock_acquired():
        with transaction.atomic():
            assert pglock.model(User, User, timeout=0)
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    not_acquired = threading.Thread(target=assert_lock_not_acquired)
    acquired = threading.Thread(target=assert_lock_acquired)
    not_acquired.start()
    acquired.start()
    not_acquired.join()
    acquired.join()


@pytest.mark.django_db()
def test_model_timeout(reraise):
    barrier = threading.Barrier(2)

    @reraise.wrap
    def assert_lock_timeout():
        barrier.wait(timeout=5)

        with transaction.atomic():
            assert not pglock.model("auth.User", timeout=pglock.timedelta(milliseconds=100))

            # Verify the transaction isn't in an errored state
            assert not Group.objects.exists()

        with transaction.atomic():
            with pytest.raises(OperationalError):
                pglock.model(
                    "auth.User",
                    timeout=pglock.timedelta(milliseconds=100),
                    side_effect=pglock.Raise,
                )

        barrier.wait(timeout=5)

    @reraise.wrap
    def assert_lock_acquired():
        with transaction.atomic():
            assert pglock.model("auth.User", timeout=0)
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    timed_out = threading.Thread(target=assert_lock_timeout)
    acquired = threading.Thread(target=assert_lock_acquired)
    timed_out.start()
    acquired.start()
    timed_out.join()
    acquired.join()


@pytest.mark.django_db()
def test_prioritize(reraise):
    barrier = threading.Barrier(2)

    @reraise.wrap
    def assert_prioritized():
        barrier.wait(timeout=5)

        with transaction.atomic():
            with pglock.prioritize(
                interval=pglock.timedelta(milliseconds=100),
                side_effect=pglock.Terminate(blocking_activity__duration__gte="0 seconds"),
            ):
                assert pglock.model(
                    "auth.User",
                    timeout=pglock.timedelta(milliseconds=300),
                    side_effect=pglock.Raise,
                )
                barrier.wait(timeout=5)

    @reraise.wrap
    def assert_terminated():
        with pytest.raises(OperationalError, match="terminat"):
            with transaction.atomic():
                assert pglock.model("auth.User", timeout=0)
                barrier.wait(timeout=5)
                barrier.wait(timeout=5)

    prioritized = threading.Thread(target=assert_prioritized)
    terminated = threading.Thread(target=assert_terminated)
    prioritized.start()
    terminated.start()
    prioritized.join()
    terminated.join()


@pytest.mark.django_db()
def test_prioritize_bad_side_effect(reraise):
    # Create a side effect that raises an error to ensure the
    # exception is propagated
    barrier = threading.Barrier(2)

    @reraise.wrap
    def assert_prioritized():
        barrier.wait(timeout=5)

        with pytest.raises(RuntimeError, match="raised in side effect"):
            with transaction.atomic():
                with pglock.prioritize(
                    interval=pglock.timedelta(milliseconds=100),
                    # duration_gte is an invalid filter
                    side_effect=pglock.Terminate(blocking_activity__duration_gte="5 minutes"),
                ):
                    assert pglock.model(
                        "auth.User",
                        timeout=pglock.timedelta(milliseconds=300),
                        side_effect=pglock.Raise,
                    )
        barrier.wait(timeout=5)

    @reraise.wrap
    def hold_lock():
        with transaction.atomic():
            assert pglock.model("auth.User", timeout=0)
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    prioritized = threading.Thread(target=assert_prioritized)
    hold = threading.Thread(target=hold_lock)
    prioritized.start()
    hold.start()
    prioritized.join()
    hold.join()


@pytest.mark.django_db()
def test_prioritize_timeout(reraise):
    barrier = threading.Barrier(2)

    @reraise.wrap
    def assert_lock_timeout():
        barrier.wait(timeout=5)

        with pytest.raises(OperationalError, match="timeout"):
            with transaction.atomic():
                with pglock.prioritize(
                    interval=0.3,
                    timeout=pglock.timedelta(milliseconds=100),
                ):
                    assert pglock.model(
                        "auth.User",
                        side_effect=pglock.Raise,
                    )

        barrier.wait(timeout=5)

    @reraise.wrap
    def assert_terminated():
        with transaction.atomic():
            assert pglock.model("auth.User", timeout=0)
            barrier.wait(timeout=5)
            barrier.wait(timeout=5)

    timed_out = threading.Thread(target=assert_lock_timeout)
    terminated = threading.Thread(target=assert_terminated)
    timed_out.start()
    terminated.start()
    timed_out.join()
    terminated.join()


def test_prioritize_args():
    with pytest.raises(TypeError, match="must be an int"):
        with pglock.prioritize(interval="1"):
            pass

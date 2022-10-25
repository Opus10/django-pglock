.. _advisory:

Advisory Locks
==============

Sometimes applications need to perform custom locking that cannot be easily expressed as
row or table locks. The `pglock.advisory` decorator and context manager solves this by using
`Postgres advisory locks <https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS>`__.

Basic Examples
--------------

`pglock.advisory` is a decorator and context manager that can be used to acquire an advisory lock.
Once the lock is acquired, all other code requesting the lock will wait until the lock is released.

For example, below we've ensured there's only one instance of ``my_exclusive_function``
running at a time:

.. code-block:: python

    import pglock

    @pglock.advisory("my_module.my_exclusive_function")
    def my_exclusive_function():
        # All other calls of my_exclusive_function will wait for this one to finish

.. tip::

    When used as a decorator, the lock ID isn't required
    and will default to ``<module_name>.<function_name>``.

When creating an advisory lock, remember that the lock ID is a global name across the entire database. Be sure
to choose meaningful names, ideally with namespaces, when serializing code with `pglock.advisory`.

Configuring Lock Wait Time
--------------------------

By default, `pglock.advisory` will wait forever until the lock can be acquired. Use the
``timeout`` argument to change this behavior.
For example, ``timeout=0`` will avoid waiting for the lock:

.. code-block:: python

    with pglock.advisory("my_lock_id", timeout=0) as acquired:
        if not acquired:
            # Do stuff if the lock cannot be acquired
        else:
            # The lock was acquired

As shown above, the context manager returns a flag to indicate if the lock was successfully acquired.
Here we wait up to two seconds to acquire the lock:

.. code-block:: python

    with pglock.advisory("my_lock_id", timeout=2) as acquired:
        ...

.. tip::

    Use a `datetime.timedelta` argument for ``timeout`` for better precision or ``None`` to
    set an infinite timeout.

Side Effects
------------

The ``side_effect`` argument adjusts runtime characteristics when using a timeout.
For example, below we're using ``timeout=0`` and ``side_effect=pglock.Raise`` to raise an exception
if the lock cannot be acquired:

.. code-block:: python

    with pglock.advisory(timeout=0, side_effect=pglock.Raise):
        # A django.db.utils.OperationalError will be thrown if the lock cannot be acquired.

.. note::

    When using the decorator, the side effect defaults to `pglock.Raise` since the return
    value is not available to the decorated function.

Use ``side_effect=pglock.Skip`` to skip the function entirely if the lock cannot be acquired.
This only applies to usage of the decorator:

.. code-block:: python

    @pglock.advisory(timeout=0, side_effect=pglock.Skip)
    def one_function_at_a_time():
        # This function runs once at a time. If this function runs anywhere else, it will be skipped.

Shared Locks
------------

Advisory locks can be acquired in shared mode using ``shared=True``. Shared locks do not conflict with
other shared locks. They only conflict with other exclusive locks of the same lock ID.
See the
`Postgres docs <https://www.postgresql.org/docs/current/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS-TABLE>`__
for more information.
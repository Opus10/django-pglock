.. _command:

Management Command
==================

Use ``python manage.py pglock`` to view, filter, and kill locks.

Basic Usage
-----------

Running ``python manage.py pglock`` will show a list of active locks.
Fields are separated by ``|`` and are configured with
``settings.PGLOCK_ATTRIBUTES``, which defaults to the following:

1. **activity_id**: The process ID that's using the lock.
2. **wait_duration**: The time the lock has been waiting, or ``None`` if it's
   acquired.
3. **mode**: The mode of the lock, such as "ACCESS_EXCLUSIVE".
4. **rel_kind**: The kind of relationship, such as "TABLE".
5. **rel_name**: The name of the relationship locked, such as the table name.
6. **activity__context**: Application context tracked by
   `django-pgactivity <https://django-pgactivity.readthedocs.io>`__.
7. **activity__query**: The SQL of the query.

Output looks like the following::

    76362 | 21:02:35 | ACCESS_EXCLUSIVE | TABLE | auth_user | None | lock table auth_user in access
    246 | None | ACCESS_SHARE | INDEX | pg_class_tblspc_relfilenode_index | None | WITH _pgactivity
    246 | None | ACCESS_SHARE | INDEX | pg_class_relname_nsp_index | None | WITH _pgactivity_activi
    246 | None | ACCESS_SHARE | INDEX | pg_class_oid_index | None | WITH _pgactivity_activity_cte A
    96277 | None | ACCESS_EXCLUSIVE | TABLE | auth_user | None | lock table auth_user in access exc

.. note::

    Locks are always ordered in descending order by wait duration.

Use ``-e`` (or ``--expanded``) to avoid truncating results::

    ───────────────────────────────────────────────────────────────────────────────────────────────
    activity_id: 76362
    wait_duration: 21:03:53
    mode: ACCESS_EXCLUSIVE
    rel_kind: TABLE
    rel_name: auth_user
    activity__context: None
    activity__query: lock table auth_user in access exclusive mode;
    ───────────────────────────────────────────────────────────────────────────────────────────────
    activity_id: 514
    wait_duration: None
    mode: ACCESS_SHARE
    rel_kind: INDEX
    rel_name: pg_class_tblspc_relfilenode_index
    ...

.. note::

    Query SQL will always be truncated to a max of 1024 characters by
    default unless ``track_activity_query_size`` is configured on your Postgres
    server. We highly recommend attaching context to queries using
    `django-pgactivity <https://django-pgactivity.readthedocs.io>`__ to better
    understand what parts of your application are acquiring locks.

Use ``-f`` (or ``--filter``) to filter results. Below we filter for locks that have wait durations
greater than five seconds on tables::

    python manage.py pglock -f "wait_duration__gt=5 seconds" -f rel_kind=TABLE

.. tip::

    The ``-f`` flag just passes filters to the ``.filter()`` method on the `PGLock` queryset.
    You can filter on any attribute or relation of the `PGLock` model.

Use ``--blocking`` to show locks that are blocked by other locks. The output fields are
configured with ``settings.PGLOCK_BLOCKING_ATTRIBUTES`` and default to:

1. **activity_id**: The process ID that's using the lock.
2. **blocking_activity_id**: The process ID that's blocking the lock.
3. **activity__context**: Application context of the blocked query tracked by
   `django-pgactivity <https://django-pgactivity.readthedocs.io>`__.
4. **blocking_activity__context**: Application context of the blocking
   query tracked by `django-pgactivity <https://django-pgactivity.readthedocs.io>`__.
5. **activity__query**: The SQL of the blocked query.
6. **blocking_activity__query**: The SQL of the blocking query.

Canceling and Terminating Queries
---------------------------------

Use ``--cancel`` or ``--terminate`` to issue ``pg_cancel_backend`` or
``pg_terminate_backend`` requests to all matching results. For example,
the following will terminate every active session, including the
one issuing the management command::

    python manage.py pglock --terminate

Normally one will first use the ``pglock`` command to find the process
IDs they wish to terminate and then supply them like so::

    python manage.py pglock pid1 pid2 --terminate

You'll be prompted before termination and can disable this
with ``-y`` (or ``--yes``).

Supply the ``--blocking`` flag with ``--cancel`` or ``--terminate``
to cancel or terminate all *blocking* queries of the activity.
For example, this will kill all blocking queries of activity
that has been blocked for over a minute::

    python manage.py pglock -f "wait_duration__gt=1 minute" --blocking --terminate

Re-usable Configurations
------------------------

Use ``settings.PGLOCK_CONFIGS`` to store and load re-usable parameters
with ``-c`` (or ``--config``). For example, here we've made a configuration
to kill all blocking activity for locks that have waited longer than a minute:

.. code-block:: python

    PGLOCK_CONFIGS = {
        "kill-long-blocking": {
            "filters": ["wait_duration__gt=1 minute"],
            "yes": True,
            "blocking": True,
            "terminate": True
        }
    }

We can use this configuration like so::

    python manage.py pglock -c kill-long-blocking

.. tip::

    The keys for configuration dictionaries directly match the management command
    argument destinations.
    Do ``python manage.py pglock -h`` to see the destinations, which are
    uppercase. Arguments that can be supplied multiple times,
    such as ``-f`` (i.e. the "filters" argument) are provided as lists.

Here's another example of a configuration that changes the output fields of
the ``pglock`` command:

.. code-block:: python

    PGLOCK_CONFIGS = {
        "short-output": {
            "attributes": ["activity_id", "wait_duration"]
        }
    }

When using ``-c short-output``, only the wait duration and activity IDs will
be shown by default.

.. tip::

    You can still use a command arguments when using a configuration. 
    Command line arguments override configurations, and configurations
    override global :ref:`settings`.

All Options
-----------

Here's a list of all options to the ``pglock`` command:

[pids ...]
    Process IDs to filter by.

-d, --database  The database.
-f, --filter  Filters for the underlying queryset. Can be used multiple times.
-o, --on  Filter by model. A passthrough for PGLock.objects.on().
--blocking  Show blocking locks
-a, --attribute  Attributes to show when listing locks. Defaults to ``settings.PGLOCK_ATTRIBUTES``.
                 If ``--blocking`` is used, defaults to ``settings.PGLOCK_BLOCKING_ATTRIBUTES``.
-l, --limit  Limit results. Defaults to ``settings.PGLOCK_LIMT``.
-e, --expanded   Show an expanded view of results.
-c, --config  Use a config from ``settings.PGLOCK_CONFIGS``.
--cancel  Cancel matching activity.
--terminate  Terminate activity.
-y, --yes  Don't prompt when canceling or terminating activity.

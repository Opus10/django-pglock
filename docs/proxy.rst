.. _proxy:

Proxy Models
============

The `pglock.models.PGLock` and `pglock.models.BlockedPGLock`
models provide a wrapper around
`Postgres's pg_locks view <https://www.postgresql.org/docs/current/view-pg-locks.html>`__.
Both models foreign key to the ``PGActivity`` model from
`django-pgactivity <https://django-pgactivity.readthedocs.io>`__, making
it possible to join other information such as the SQL and transaction ID. View
the `django-pgactivity docs <https://django-pgactivity.readthedocs.io>`__ for more information.


PGLock
------

`PGLock` lets you query all active locks. It includes some of the following attributes:

* **activity**: The activity associated with the lock. This is a foreign key
  to `django-pgactivity's PGActivity model <https://django-pgactivity.readthedocs.io/en/latest/index.html>`__,
  which includes the SQL query.
* **mode**: The lock mode.
* **granted**: A boolean indicating if the lock has been granted or is still waiting to be acquired.
* **wait_duration**: How long the lock has been waiting to be acquired. Only available in Postgres 14 and up.
* **rel_kind**: The type of relation for the lock, such as "TABLE" or "INDEX".
* **rel_name**: The name of the relation, such as the table name.

See `PGLock` for a list of all attributes and possible options for fields.

For example, this query will show all locks that are blocked. It will
also show which query is trying to acquire the locks.

.. code-block:: python

    from datetime import timedelta
    from pglock.models import PGLock

    PGLock.objects.filter(
        granted=False
    ).values("rel_kind", "rel_name", "activity__duration", "activity__query")

There are some special queryset methods worth noting:

* ``PGLock.objects.on(model1, model2)``: Takes a variable amount of model classes and filters locks for them.
* ``PGLock.objects.filter(...).cancel_activity()``: Cancels the query for all locks that match the queryset.
* ``PGLock.objects.filter(...).terminate_activity()``: Terminates the activity for all locks that match the queryset.

BlockedPGLock
-------------

`BlockedPGLock` inherits `PGLock`. It adds a ``blocking_activity`` attribute that references the activity
preventing a lock from being acquired. If there are multiple blocking activities for a lock, multiple models
will be returned for each blocking activity.

Along with the methods from the `PGLock` queryset, the `BlockedPGLock` queryset comes with the following
methods:

* ``BlockedPGLock.objects.filter(...).cancel_blocking_activity()``: Cancels the blocking query for all locks that match the queryset.
* ``BlockedPGLock.objects.filter(...).terminate_blocking_activity()``: Terminates the blocking activity for all locks that match the queryset.

Annotating Query Context
------------------------

`pglock.models.PGLock` has an ``activity`` foreign key to a ``pgactivity.models.PGActivity`` model.
In this model are two primary fields for understanding what query is blocked:

1. The ``query`` field, which shows the raw SQL. This SQL is truncated to 1024 characters by Postgres by
   default.
2. The ``context`` JSONField, which allows your application to attach additional context about
   the query.

We recommend `reading the django-pgactivity docs <https://django-pgactivity.readthedocs.io>`__ for more
information on how to turn on context tracking. After installing the associated middleware, you are able
to see the URL of the request that's making queries, making it much easier to understand what requests are
obtaining locks or blocking other processes.

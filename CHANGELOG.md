# Changelog
## 1.1.0 (2022-11-03)
### Bug
  - Fix PG10-13 issues. [Wesley Kendall, bf2036b]

    The waitstart column in the pg_locks view wasn't introduced until Postgres14.
    If using earlier versions, ``django-pglock`` will return null for these columns.

## 1.0.0 (2022-10-25)
### Api-Break
  - Initial release of ``django-pglock`` [Wesley Kendall, 731e0cc]

    ``django-pglock`` performs advisory locks, table locks, and helps manage blocking locks.
    Here's some of the functionality at a glance:

    * ``pglock.advisory`` for application-level locking, for example, ensuring that tasks don't overlap.
    * ``pglock.model`` for locking an entire model.
    * ``pglock.timeout`` for dynamically setting the timeout to acquire a lock.
    * ``pglock.prioritize`` to kill blocking locks for critical code, such as migrations.
    * The ``PGLock`` and ``BlockedPGLock`` models for querying active and blocked locks.
    * The ``pglock`` management command that wraps the models and provides other utilities.


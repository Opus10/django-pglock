# Changelog

## 1.5.1 (2024-04-06)

#### Trivial

  - Fix ReadTheDocs builds. [Wesley Kendall, f1b9c5c]

## 1.5.0 (2023-11-26)

#### Feature

  - Django 5.0 compatibility [Wesley Kendall, 5197c72]

    Support and test against Django 5 with psycopg2 and psycopg3.

## 1.4.1 (2023-10-09)

#### Trivial

  - Added Opus10 branding to docs [Wesley Kendall, 80e8466]

## 1.4.0 (2023-10-08)

#### Feature

  - Add Python3.12 support and use Mkdocs for documentation [Wesley Kendall, d706755]

    Python 3.12 and Postgres 16 are supported now, along with having revamped docs using Mkdocs and the Material theme.

    Python 3.7 support was dropped.

## 1.3.0 (2023-06-08)

#### Feature

  - Added Python 3.11, Django 4.2, and Psycopg 3 support [Wesley Kendall, 62c86bf]

    Adds Python 3.11, Django 4.2, and Psycopg 3 support along with tests for multiple Postgres versions. Drops support for Django 2.2.

## 1.2.0 (2023-05-08)

#### Feature

  - Support PG15 [Wesley Kendall, 31edec7]

    PG15 is supported and tested

#### Trivial

  - Updated with the latest Python project template [Wesley Kendall, 109f794]

## 1.1.0 (2022-11-04)

#### Bug

  - Fix PG10-13 issues. [Wesley Kendall, bf2036b]

    The waitstart column in the pg_locks view wasn't introduced until Postgres14.
    If using earlier versions, ``django-pglock`` will return null for these columns.

## 1.0.0 (2022-10-25)

#### Api-Break

  - Initial release of ``django-pglock`` [Wesley Kendall, 731e0cc]

    ``django-pglock`` performs advisory locks, table locks, and helps manage blocking locks.
    Here's some of the functionality at a glance:

    * ``pglock.advisory`` for application-level locking, for example, ensuring that tasks don't overlap.
    * ``pglock.model`` for locking an entire model.
    * ``pglock.timeout`` for dynamically setting the timeout to acquire a lock.
    * ``pglock.prioritize`` to kill blocking locks for critical code, such as migrations.
    * The ``PGLock`` and ``BlockedPGLock`` models for querying active and blocked locks.
    * The ``pglock`` management command that wraps the models and provides other utilities.

from django.core.exceptions import ImproperlyConfigured
from django.utils.version import get_version_tuple


def _psycopg_version():
    try:
        import psycopg as Database
    except ImportError:
        import psycopg2 as Database
    except Exception as exc:  # pragma: no cover
        raise ImproperlyConfigured("Error loading psycopg2 or psycopg module") from exc

    version_tuple = get_version_tuple(Database.__version__.split(" ", 1)[0])

    if version_tuple[0] not in (2, 3):  # pragma: no cover
        raise ImproperlyConfigured(f"Pysocpg version {version_tuple[0]} not supported")

    return version_tuple


psycopg_version = _psycopg_version()
psycopg_maj_version = psycopg_version[0]


def pg_maj_version(cursor):
    """Return the major version of Postgres that's running"""
    version = getattr(cursor.connection, "server_version", cursor.connection.info.server_version)
    return int(str(version)[:-4])

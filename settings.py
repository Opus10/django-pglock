import os

import dj_database_url


SECRET_KEY = "django-pglock"
# Install the tests as an app so that we can make test models
INSTALLED_APPS = [
    "pglock",
]

# Conditionally add the test app when we aren't building docs,
# otherwise sphinx builds won't work
if not os.environ.get("SPHINX"):
    INSTALLED_APPS += ["pglock.tests"]

# Database url comes from the DATABASE_URL env var
DATABASES = {"default": dj_database_url.config()}

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

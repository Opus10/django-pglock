import pytest
from django.db import transaction
from django.db.utils import OperationalError

from pglock import models


@pytest.mark.django_db
def test_activity_management():
    with pytest.raises(OperationalError), transaction.atomic():
        models.PGLock.objects.cancel_activity()

    with pytest.raises(OperationalError), transaction.atomic():
        models.PGLock.objects.terminate_activity()


@pytest.mark.django_db
def test_blocking_activity_management():
    models.BlockedPGLock.objects.cancel_blocking_activity()
    models.BlockedPGLock.objects.terminate_blocking_activity()

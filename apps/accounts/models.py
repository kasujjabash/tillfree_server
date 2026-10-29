from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Staff user. Customers are separate (phone + OTP) and come in Phase 2."""

    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return self.get_full_name() or self.username

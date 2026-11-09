from django.db import models

from common.tenancy.models import TenantModel


class Widget(TenantModel):
    name = models.CharField(max_length=100)

    def __str__(self) -> str:
        return self.name

from django.contrib import admin
from apps.client.models import Tenant, LicenseType, License, LicenseHistory, LicenseRenewal

# Register your models here.


class BaseAdmin(admin.ModelAdmin):
    readonly_fields = ("created_at", "updated_at")
    
    
    def get_list_display(self, request):
        return tuple(field.name for field in self.model._meta.fields)


@admin.register(
    Tenant,
    License,
    LicenseType,
    LicenseHistory,
    LicenseRenewal
)

class TenantAdmin(BaseAdmin):
    pass
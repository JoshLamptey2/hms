from django.db import models
from django.utils.translation import gettext_lazy as t 
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth import get_user_model
import uuid
import secrets
import arrow

User = get_user_model()


# Create your models here.
class Tenant(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True,editable=False)
    name = models.CharField(max_length=150,unique=True, null=False, blank= False)
    schema_name = models.CharField(max_length=63, unique=True)
    uid = models.UUIDField(default=uuid.uuid4, unique=True,editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=False)
    org_slug = models.SlugField(max_length=255)
    logo = models.ImageField(verbose_name=t("Tenant Logo"), default="tenant_logo/tenant_logo.png",upload_to="tenant/logo",blank=True,null=True)
    email = models.EmailField(verbose_name=t("Tenant Email"), unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = t("tenant")
        verbose_name_plural = t("tenant")
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.org_slug:
            slug = self.email.split("@")[1]
            self.org_slug = slug.split(".")[0]
        return super().save(*args, **kwargs)

    
class LicenseType(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, unique=True,editable=False)
    name = models.CharField(max_length=150, unique=True)
    coverage = models.TextField(null=True, blank=True)
    sub_name = models.CharField(max_length=150, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    duration = models.IntegerField(default=0)
    max_users = models.PositiveIntegerField(default=10)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = t("License Type")
        verbose_name_plural = t("License Types")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} | {self.sub_name}" if self.sub_name else f"{self.name}"

    @property
    def full_text(self):
        return f"{self.name} | {self.sub_name}" if self.sub_name else f"{self.name}"


class License(models.Model):
    class LicenseStatus(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACTIVE = "active", _("Active")
        EXPIRED = "expired", _("Expired")
        REVOKED = "revoked", _("Revoked")
        
    uid = models.UUIDField(default=uuid.uuid4, unique=True,editable=False)
    license_type = models.ForeignKey(LicenseType, on_delete=models.CASCADE, related_name="licenses")
    issue_date = models.DateField(verbose_name=_("Issue Date"), auto_now_add=True)
    expiry_date = models.DateField(verbose_name=_("Expiry Date"))
    quantity = models.PositiveIntegerField(verbose_name=_("License Qty"), default=1)
    status = models.CharField(max_length=20, choices=LicenseStatus.choices, default=LicenseStatus.PENDING)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="licenses")
    license_key = models.CharField(max_length=255, unique=True, editable=False, verbose_name=_("License Key"))
    users =models.ManyToManyField(User, verbose_name=_("Users"), related_name="user_licenses", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    
    class Meta:
        verbose_name = _("License")
        verbose_name_plural = _("Licenses")
        constraints = [
            models.UniqueConstraint(
                fields=["license_type", "tenant"],
                name="unique_license_per_tenant",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.name}"

    @property
    def name(self):
        return (
            f"{self.license_type.name} | {self.license_type.sub_name}"
            if self.license_type.sub_name
            else f"{self.license_type.name}"
        )
        
        
    @property
    def remaining_slots(self):
        return self.quantity - self.users.count()

    @property
    def license_info(self):
        return self.license_type.full_text

    @property
    def is_active(self):
        if (
            self.status == self.LicenseStatus.ACTIVE
            and timezone.now().date() < self.expiry_date
        ):
            return True
        return False

    def generate_license_key(self):
        """Generate a unique 64-character hex key and ensure it's not duplicated."""
        while True:
            key = secrets.token_hex(16)
            if not License.objects.filter(license_key=key).exists():
                return key

    def save(self, **kwargs):
        """Set expiry date, enforce max users, and generate a unique key before saving."""
        if not self.expiry_date:
            self.expiry_date = timezone.now().date() + timedelta(
                days=self.license_type.duration
            )

        if self.quantity > self.license_type.max_users:
            self.quantity = self.license_type.max_users

        if not self.license_key:
            self.license_key = self.generate_license_key()

        # Auto-expire if expiry_date has passed
        if (
            self.status == self.LicenseStatus.ACTIVE
            and timezone.now().date() >= self.expiry_date
        ):
            self.status = self.LicenseStatus.EXPIRED

        super().save(**kwargs)
        
    
    

class LicenseRenewal(models.Model):
    license = models.ForeignKey(License, on_delete=models.CASCADE, related_name="renewals")
    quantity = models.PositiveIntegerField(default=1)
    renewal_date = models.DateField(auto_now_add=True)
    expiration_date = models.DateField(default=arrow.now().shift(months=+1).date())
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    
    def __str__(self):
        return f"Renewal for {self.license} on {self.renewal_date}"
    
    def save(self, **kwargs):
        #set the expiration date if not set
        if not self.expiration_date:
            self.expiration_date = self.renewal_date + timedelta(
                days=self.license.license_type.duration
            )
            
        if self.quantity > self.license.license_type.max_users:
            self.quantity = self.license.license_type.max_users
            
        if self.quantity == 0:
            self.quantity = self.license.quantity
        else:
            self.license.quantity = self.quantity
            
            
        # Update the license's expiry date
        self.license.expiry_date = self.expiration_date
        self.license.status = self.license.LicenseStatus.ACTIVE
        self.license.save(update_fields=["expiry_date", "quantity", "status"])

        return super().save(**kwargs)
    
    
    
    class Meta:
        verbose_name = "License Renewal"
        verbose_name_plural = "License Renewals"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Renewal for {self.license}"



class LicenseHistory(models.Model):
    ACTION_CHOICES = [
        ("CREATE", "Create"),
        ("UPDATE", "Update"),
        ("DELETE", "Delete"),
        ("RENEW", "Renew"),  # New action for renewals
    ]
    
    license = models.ForeignKey(License, on_delete=models.CASCADE, related_name="history")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="license_history")
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.action} action on {self.license or self.renewal} at {self.timestamp}"

    def __str__(self):
        return f"{self.license}"
    
    


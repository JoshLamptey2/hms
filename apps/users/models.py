import uuid
import arrow
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as t
from django.contrib.auth.hashers import make_password
from apps.notifications.service import NotificationService
from phonenumber_field.modelfields import PhoneNumberField
from django.contrib.auth.models import Permission
from decouple import config
from django.utils import timezone

service = NotificationService()


class UserGroup(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    users = models.ManyToManyField(
        "users.User", verbose_name="user_perm_group", blank=True
    )
    permissions = models.ManyToManyField(
        Permission,
        blank=True,
        related_name="user_groups",
    )
    tenant = models.ForeignKey(
        "client.Tenant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_groups",
    )
    is_global = models.BooleanField(default=False)
    name = models.CharField(max_length=150, null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Group"
        verbose_name_plural = "User Groups"
        ordering = ["-created_at"]


class UserRole(models.Model):
    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", t("Super Admin")
        STAFF = "STAFF", t("Staff")
        CUSTOMER = "CUSTOMER", t("Customer")

    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=50, choices=Role.choices, default=Role.CUSTOMER,unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = t("User Role")
        verbose_name_plural = t("User Roles")
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class User(AbstractUser):
    class Gender(models.TextChoices):
        MALE = "MALE", t("Male")
        FEMALE = "FEMALE", t("Female")
        OTHER = "OTHER", t("Other")

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", t("Active")
        INACTIVE = "INACTIVE", t("Inactive")
        SUSPENDED = "SUSPENDED", t("Suspended")

    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    username = models.CharField(max_length=150, unique=True)
    middle_name = models.CharField(max_length=150, blank=True, null=True)
    role = models.ForeignKey(
        UserRole, on_delete=models.SET_NULL, related_name="users", blank=True, null=True
    )
    phone_number = PhoneNumberField()
    gender = models.CharField(
        max_length=10, choices=Gender.choices, default=Gender.OTHER
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.ACTIVE
    )
    org_slug = models.CharField(max_length=150, blank=True, null=True)
    tenant = models.ForeignKey(
        "client.Tenant",
        on_delete=models.SET_NULL,
        related_name="users",
        blank=True,
        null=True,
    )
    image = models.ImageField(upload_to="users/", null=True, blank=True)
    password = models.CharField(max_length=128, blank=True, null=True)
    is_blocked = models.BooleanField(default=False)
    blocked_at = models.DateTimeField(blank=True, null=True)
    password_changed = models.BooleanField(default=False)
    password_expiry = models.DateTimeField(blank=True, null=True)
    login_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = t("User")
        verbose_name_plural = t("Users")
        ordering = ["-created_at"]

    def generate_temporary_password(self):
        temp_password = uuid.uuid4().hex[:12]
        return temp_password
    

    def save(self, *args, **kwargs):
        temp_password = None

        if not self.password:
            temp_password = self.generate_temporary_password()
            self.set_password(temp_password)
            self.password_changed = False
            self.password_expiry = arrow.now().shift(days=+7).datetime

        super().save(*args, **kwargs)

        if temp_password and self.email:
            email = self.email

            full_name = self.get_full_name()
            service.send_login_credentials(
                to=email,
                password=temp_password,
                full_name=full_name,
            )

    def is_password_expired(self):
        if self.password_expiry and timezone.now() > self.password_expiry:
            return True
        return False

    def __str__(self):
        return self.username

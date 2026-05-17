from rest_framework.permissions import BasePermission
from rest_framework.exceptions import PermissionDenied, NotAuthenticated
from django.contrib.auth.models import Permission
from apps.users.models import UserRole, UserGroup
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone
from django.db.models import Q
from apps.client.models import License
import jwt
import logging
from django.core.cache import cache
from decouple import config
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class CustomPermission(BasePermission):
    """
    Handles:
    -Token Validation
    - Role-based access
    - Group & model permissions
    - Organization context
    - License validation
    """

    def has_permission(self, request, view):

        user = request.user

        if not user or not user.is_authenticated:
            raise NotAuthenticated("Authentication credentials were not provided.")

        if user.is_blocked:
            raise PermissionDenied(
                "Your account is temporarily blocked. Please contact support."
            )

        if hasattr(user, "role") and user.role:
            if user.role.name == UserRole.Role.SUPER_ADMIN:
                return True

        model_name = view.content_model._meta.model_name
        permission_map = {
            "create": f"add_{model_name}",
            "update": f"change_{model_name}",
            "partial_update": f"change_{model_name}",
            "destroy": f"delete_{model_name}",
            "retrieve": f"view_{model_name}",
            "list": f"view_{model_name}",
        }
        required_permission = permission_map.get(view.action)

        if required_permission:
            # First check if the permission exists in the system
            content_type = ContentType.objects.get_for_model(view.content_model)
            if not Permission.objects.filter(
                codename=required_permission, content_type=content_type
            ).exists():
                return False

            # Check if user is in any group that has this permission
            user_has_group_permission = UserGroup.objects.filter(
                Q(users=request.user)
                & Q(permissions__codename=required_permission)
                & Q(permissions__content_type=content_type)
                & Q(tenant=request.user.tenant) | Q(is_global=True)
            ).exists()

            if not user_has_group_permission:
                # If no group permission, check if user has direct permission
                if not request.user.has_perm(
                    f"{content_type.app_label}.{required_permission}"
                ):
                    return False

        # Check organization context
        if (
            hasattr(view, "check_organization_context")
            and view.check_organization_context
        ):
            if not self._check_organization_context(request, view):
                return False

        # License validation

        if not License.objects.filter(
            users=request.user,
            status=License.LicenseStatus.ACTIVE,
            expiry_date__gt=timezone.now(),
        ).exists():
            raise PermissionDenied(
                "Your organization does not have a valid license. Please contact support."
            )

        return True

    def _check_organization_context(self, request, view):
        """Helper method to verify organization context for the request"""
        user = request.user
        organisation = getattr(user, "tenant", None)

        if not organisation:
            return False

        # If the view has an organization field in its queryset, filter by it
        if hasattr(view, "get_queryset"):
            queryset = view.get_queryset()
            if hasattr(queryset.model, "organisation"):
                return queryset.filter(organisation=organisation).exists()

        return True

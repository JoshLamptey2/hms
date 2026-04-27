import random
import re
import string
import logging
import arrow
import os
import jwt
from django.conf import settings
from django.core.cache import cache
from rest_framework.decorators import action
from django.db.models import Q
from django.contrib.auth.models import Permission
from django.http import FileResponse, Http404
from apps.client.models import License, Tenant, RefreshToken
from apps.client.serializers import LicenseListSerializer
from apps.users.auth import Authenticator
from rest_framework import viewsets, status, permissions
from rest_framework.response import Response
from apps.users.models import UserGroup, UserRole, User
from apps.users.perms import CustomPermission
from apps.users.serializers import (
    UserRoleSerializer,
    UserCreateSerializer,
    UserAdminUpdateSerializer,
    UserSelfUpdateSerializer,
    UserListSerializer,
    UserGroupCreateUpdateSerializer,
    UserGroupListSerializer,
)
from rest_framework.throttling import (
    ScopedRateThrottle,
    UserRateThrottle,
)
from rest_framework.exceptions import PermissionDenied
from apps.notifications.service import NotificationService
from drf_spectacular.utils import extend_schema, OpenApiResponse, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from decouple import config
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from apps.users.pagination import FetchDataPagination

auth = Authenticator()
logger = logging.getLogger(__name__)
service = NotificationService()


class UserRoleViewset(viewsets.ModelViewSet):
    content_model = UserRole
    queryset = UserRole.objects.all()
    serializer_class = UserRoleSerializer
    permission_classes = [CustomPermission]
    lookup_field = "uid"

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "info": serializer.data,
            }
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        return Response(
            {
                "success": True,
                "info": serializer.data,
            }
        )

    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            name = data.get("name")

            if not name:
                return Response(
                    {
                        "success": False,
                        "info": "Name is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            role = UserRole.objects.filter(name=name)

            if role.exists():
                return Response(
                    {
                        "success": False,
                        "info": "Role already exists.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            serializer.save()

            return Response(
                {
                    "success": True,
                    "info": "Role created successfully.",
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.error(f"Error creating role: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to create role.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


EXCLUDED_APPS = [
    "admin",
    "auth",
    "contenttypes",
    "sessions",
    "staticfiles",
    "rest_framework",
    "drf_spectacular",
    "post_office",
    "django_celery_results",
    "django_celery_beat",
]

CLIENT_EXCLUDED_APPS = [
    "client",
]

ALL_EXCLUDED_APPS = EXCLUDED_APPS + CLIENT_EXCLUDED_APPS


class PermissionViewset(viewsets.ViewSet):
    """
    ViewSet to list all available permissions in the system.
    """

    @extend_schema(
        summary="List all permissions",
        description="Returns all available permissions in the system grouped by model name",
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Permissions grouped by model",
                examples=[
                    OpenApiExample(
                        "Success Response",
                        value={
                            "success": True,
                            "info": {
                                "user": [
                                    {
                                        "id": 1,
                                        "name": "Can add user",
                                        "codename": "add_user",
                                    },
                                    {
                                        "id": 2,
                                        "name": "Can change user",
                                        "codename": "change_user",
                                    },
                                ],
                                "license": [
                                    {
                                        "id": 10,
                                        "name": "Can view license",
                                        "codename": "view_license",
                                    }
                                ],
                            },
                        },
                    )
                ],
            )
        },
    )
    def list(self, request, *args, **kwargs):
        permissions = Permission.objects.select_related("content_type")
        grouped_permissions = {}

        user = request.user
        is_super_admin = (
            user.is_authenticated
            and getattr(user, "role", None)
            and user.role.name == "SUPER_ADMIN"
        )

        for perm in permissions:
            app_label = perm.content_type.app_label

            if app_label in ALL_EXCLUDED_APPS:
                continue

            if not is_super_admin and app_label in CLIENT_EXCLUDED_APPS:
                continue

            model_name = perm.content_type.model

            grouped_permissions.setdefault(model_name, []).append(
                {
                    "id": perm.id,
                    "name": perm.name,
                    "codename": perm.codename,
                }
            )

        return Response(
            {
                "success": True,
                "info": grouped_permissions,
            },
            status=status.HTTP_200_OK,
        )

    # I don't understand this yet i will touch it tomorrow
    # @action(detail=False, methods=["get"], url_path="fetch-organization-permissions")
    # def fetch_organization_permissions(self, request):
    #     try:
    #         permissions = Permission.objects.select_related("content_type").all()
    #         grouped_permissions = {}

    #         for perm in permissions:
    #             if perm.content_type.app_label in ALL_EXCLUDED_APPS:
    #                 continue

    #             model_name = perm.content_type.model  # Get model name

    #             if model_name not in grouped_permissions:
    #                 grouped_permissions[model_name] = []

    #             grouped_permissions[model_name].append(
    #                 {"id": perm.id, "codename": perm.codename, "name": perm.name}
    #             )

    #         return Response(
    #             {"success": True, "info": grouped_permissions},
    #             status=status.HTTP_200_OK,
    #         )

    #     except Exception as e:
    #         logger.warning(str(e))
    #         return Response(
    #             {
    #                 "success": False,
    #                 "info": "An error occurred while fetching permissions",
    #             },
    #             status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #         )


class UserGroupViewset(viewsets.ModelViewSet):
    content_model = UserGroup
    queryset = UserGroup.objects.prefetch_related("permissions").all()
    permission_classes = [CustomPermission]
    lookup_field = "uid"

    def get_serializer_class(self):
        if self.action in ["create", "update", "partial_update"]:
            return UserGroupCreateUpdateSerializer
        return UserGroupListSerializer

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset

        if user.role and user.role.name == UserRole.Role.SUPER_ADMIN:
            return qs.filter(is_global=True)

        return qs.filter(
            is_global=False,
            tenant=user.tenant, 
        )
        
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(
            {
                "success": True,
                "info": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)

        return Response(
            {
                "success": True,
                "info": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            name = data.get("name")
            permissions = data.get("permissions", [])

            tenant = request.user.tenant

            if not name:
                return Response(
                    {
                        "success": False,
                        "info": "Name is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Check if group already exists with or without organization
            group_filter = UserGroup.objects.filter(name=name)
            if tenant:
                group_filter = group_filter.filter(tenant=tenant)
            else:
                group_filter = group_filter.filter(tenant__isnull=True)

            if group_filter.exists():
                return Response(
                    {
                        "success": False,
                        "info": " User Group already exists.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Create the group with or without organization

            group_data = {"name": name}
            if tenant:
                group_data["tenant"] = tenant

            if request.user.role == UserRole.Role.SUPER_ADMIN:
                group_data["is_global"] = True

            group = UserGroup.objects.create(**group_data)

            # Assign permissions to the group
            if permissions:
                valid_permissions = Permission.objects.filter(id__in=permissions)
                group.permissions.set(valid_permissions)

            return Response(
                {
                    "success": True,
                    "info": "User Group created successfully.",
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.error(f"Error creating user group: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to create user group.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        url_path="assign-user-group",
    )
    def assign_user_group(self, request, *args, **kwargs):
        try:
            data = request.data
            group_id = data.get("group_id")
            users = data.get("users", [])

            if not group_id:
                return Response(
                    {
                        "success": False,
                        "info": "Group ID is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not isinstance(users, list):
                return Response(
                    {"success": False, "info": "users should be a list"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user_group = UserGroup.objects.filter(id=group_id).first()

            if not user_group:
                return Response(
                    {
                        "success": False,
                        "info": "User Group does not exist.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            for user in users:
                user_group.users.add(user)

            user_group.save()

            return Response(
                {
                    "success": True,
                    "info": "User Group assigned successfully.",
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error assigning user group: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to assign user group.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        url_path="remove-user-group",
    )
    def remove_user_group(self, request, *args, **kwargs):
        try:
            data = request.data
            group_id = data.get("group_id")
            users = data.get("users", [])

            if not group_id:
                return Response(
                    {
                        "success": False,
                        "info": "Group ID is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not isinstance(users, list):
                return Response(
                    {
                        "success": False,
                        "info": "users should be a list",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user_group = UserGroup.objects.filter(id=group_id).first()

            if not user_group:
                return Response(
                    {
                        "success": False,
                        "info": "User Group does not exist.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user_group.users.remove(*users)

            return Response(
                {
                    "success": True,
                    "info": "User Group removed successfully.",
                },
                status=status.HTTP_410_GONE,
            )

        except Exception as e:
            logger.error(f"Error removing user group: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to remove user group.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserViewset(viewsets.ModelViewSet):
    content_model = User
    queryset = User.objects.select_related("role").all()
    permission_classes = [CustomPermission]
    lookup_field = "uid"
    pagination_class = FetchDataPagination
    throttle_classes = [ScopedRateThrottle, UserRateThrottle]

    def get_serializer_class(self):
        if self.action == "create":
            return UserCreateSerializer

        if self.action in ["update", "partial_update"]:
            obj = self.get_object()

            if obj.id == self.request.user.id:
                return UserSelfUpdateSerializer

            return UserAdminUpdateSerializer

        return UserListSerializer

    def get_queryset(self):
        user = self.request.user
        queryset = User.objects.select_related("role", "tenant").all()

        # Scope by org for non-super-admins
        if user.role.name != "SUPER_ADMIN":
            queryset = queryset.filter(tenant__org_slug=user.org_slug)

        # Optional filters from query params
        tenant = self.request.query_params.get("tenant")
        role = self.request.query_params.get("role")
        user_status = self.request.query_params.get("status")
        name = self.request.query_params.get("name")# avoid shadowing `status` module

        if tenant and user.role.name == "SUPER_ADMIN":  # only super_admin can filter across orgs
            queryset = queryset.filter(tenant__org_slug=tenant)
        if role:
            queryset = queryset.filter(role__name__iexact=role)
        if user_status:
            queryset = queryset.filter(status__iexact=user_status)
        if name:
                queryset = queryset.filter(
                    Q(first_name__icontains=name) |
                    Q(last_name__icontains=name) |
                    Q(middle_name__icontains=name)
                )

        return queryset
    

    def check_throttles(self, request):
        method = getattr(self, self.action)

        if hasattr(method, "throttle_scope"):
            self.throttle_scope = method.throttle_scope
        super().check_throttles(request)

    def get_object(self):
        obj = super().get_object()

        user = self.request.user

        if user.role.name in ["ADMIN_USER", "SUPER_ADMIN"]:
            return obj

        if obj.id != user.id:
            raise PermissionDenied("You do not have permission to access this User")

        return obj

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)

        user = request.user

        forbidden_fields = {"tenant", "org_slug"}

        if forbidden_fields & set(serializer.validated_data.keys()):
            raise PermissionDenied("Organisation fields cannot be modified.")

        if instance.id == user.id:
            forbidden_self_fields = {
                "role",
                "status",
                "is_blocked",
                "login_enabled",
            }

            if forbidden_self_fields & set(serializer.validated_data.keys()):
                raise PermissionDenied("You cannot modify administrative fields.")
       
        self.perform_update(serializer)

        # updated_fields = serializer.validated_data.keys()

        # add the sync to other user branches that will be created later
        # sync_user_to_staff(
        #     user=instance,
        #     org_slug=instance.org_slug,
        #     updated_fields=updated_fields,
        # )

        return Response(
            {
                "success": True,
                "info": "Field Updated Successfully",
            },
            status=status.HTTP_200_OK,
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()

        serializer = self.get_serializer(instance)

        return Response(
            {
                "success": True,
                "info": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def list(self, request, *args, **kwargs):
        if request.user.role.name not in ["SUPER_ADMIN"]:
            raise PermissionDenied("You do not have permission to view this list.")
        
        
        queryset = self.filter_queryset(self.get_queryset())
        
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
            return Response({
                "success": True,
                "info": data["results"],
                "meta": {
                "count": data["count"],
                "next": data["next"],
                "previous": data["previous"],
        }
            }, status=status.HTTP_200_OK)

        serializer = self.get_serializer(queryset, many=True)

        return Response(
            {
                "success": True,
                "info": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def create(self, request, *args, **kwargs):
        try:
            
            data = request.data.copy()

            required_fields = [
                "first_name",
                "last_name",
                "role",
                "email",
                "phone_number",
                "gender",
            ]
            
            for field in required_fields:
                if not data.get(field):
                    return Response(
                        {
                            "success": False,
                            "info": f"{field} is required.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            
            if request.user.role.name != 'SUPER_ADMIN':
                data['tenant'] = request.user.tenant.id
                data['org_slug'] = request.user.org_slug
            else:
                tenant = Tenant.objects.filter(id=data.get("tenant")).first()
                if not tenant:
                    return Response({"success": False, "info": "Tenant does not exist."}, status=status.HTTP_400_BAD_REQUEST)
                data["org_slug"] = tenant.org_slug
            
            if User.objects.filter(
                Q(email=data.get("email")) | Q(phone_number=data.get("phone_number")),
                org_slug=data['org_slug'],
            ).exists():
                return Response(
                    {
                        "success": False,
                        "info": "User already exists.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            
            
            if not data.get("username"):
                
                base = f"{data.get('first_name', '').lower()}_{data.get('last_name', '').lower()}"
                username = base
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base}_{counter}"
                    counter += 1
                data["username"] = username
            
            if data.get("password"):
                try:
                    plain_password = data.get("password")
                    validate_password(plain_password)
                    data["password"] = make_password(plain_password)
                    email=data.get("email")
                    full_name = f"{data.get('first_name')} {data.get('last_name')}"
                    
                    service.send_login_credentials(
                        to = email,
                        full_name=full_name,
                        password=plain_password,
                    )
                except ValidationError as e:
                    return Response(
                        {
                            "success": False,
                            "info": e.messages,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        
            
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            
            
            self.perform_create(serializer)
            
            return Response(
                {
                    "success": True,
                    "info": "User created successfully.",
                },
                status=status.HTTP_201_CREATED,
            )
        
        except Exception as e:
            logger.exception(f"User create failed {str(e)} ")  # this logs the full traceback
            return Response({"success": False, "info": "Failed to create user."}, status=500)



    @action(detail=False, methods=["post"], url_path="update-password")
    def update_password(self, request, pk=None):
        data = request.data
        field = data.get("field")
        password = data.get("password")
        confirm_password = data.get("confirm_password")

        user = request.user

        if not field:
            return Response(
                {
                    "success": False,
                    "info": "Field is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if field not in [user.email, user.phone_number]:
            return Response(
                {"success": False, "info": "You can only update your own password."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not password:
            return Response(
                {
                    "success": False,
                    "info": "Password is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not confirm_password:
            return Response(
                {
                    "success": False,
                    "info": "Confirm Password is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if password != confirm_password:
            return Response(
                {
                    "success": False,
                    "info": "Passwords do not match.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(password, user=user)
            user.set_password(password)
            
        except ValidationError as e:
            return Response(
                {
                    "success": False,
                    "info": e.messages,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.password_changed = True
        user.login_enabled = True
        user.save(update_fields=["password", "password_changed", "login_enabled"])

        RefreshToken.objects.filter(user=user).update(is_revoked=True)
        
         # blacklist the access token in Redis
        auth_header = request.headers.get("Authorization")

        if auth_header:
            try:
                prefix, token = auth_header.split(" ")

                if prefix.lower() != "bearer":
                    return Response(
                        {
                            "success": False,
                            "info": "Invalid token prefix.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                payload = jwt.decode(
                    token,
                    config("SECRET_KEY"),
                    algorithms=["HS256"],
                    options={"verify_exp": False},
                )

                jti = payload.get("jti")
                exp = payload.get("exp")

                if jti and exp:
                    ttl = exp - int(arrow.utcnow().timestamp())

                    if ttl > 0:
                        cache.set(f"blacklist:access:{jti}", "blacklisted", timeout=ttl)
            except Exception as e:
                logger.error(f"Error blacklisting access token: {e}") 
                pass
        
        return Response(
            {
                "success": True,
                "info": "Password updated successfully. Please re-login!",
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="reset-password",
        throttle_classes=[ScopedRateThrottle],
    )
    def reset_password(self, request, pk=None):
        data = request.data
        field = data.get("field")
        reset_token = data.get("reset_token")
        password = data.get("password")
        confirm_password = data.get("confirm_password")
        
        
        if not field:
            return Response(
                {"success": False, "info": "field is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not reset_token:
            return Response(
                {"success": False, "info": "reset_token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )


        if not password:
            return Response(
                {
                    "success": False,
                    "info": "Password is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not confirm_password:
            return Response(
                {
                    "success": False,
                    "info": "Confirm Password is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if password != confirm_password:
            return Response(
                {
                    "success": False,
                    "info": "Passwords do not match.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
            
        
        # decode the reset token
        try:
            payload = jwt.decode(reset_token, config("SECRET_KEY"), algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return Response(
                {"success": False, "info": "Reset token has expired"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        except jwt.InvalidTokenError:
            return Response(
                {"success": False, "info": "Invalid reset token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # ensure token is scoped correctly
        if payload.get("type") != "password_reset":
            return Response(
                {"success": False, "info": "Invalid token type"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        identifier = payload.get("identifier")

        # verify token exists in redis and matches
        cached_token = cache.get(f"reset_token:{identifier}")
        
        if isinstance(cached_token, bytes):
            cached_token = cached_token.decode("utf-8")

        if not cached_token or cached_token != reset_token:
            return Response(
                {"success": False, "info": "Reset token is invalid or already used"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # get user from identifier in token
        try:
            user = User.objects.filter(Q(email=identifier) | Q(phone_number=identifier)).first()
        except User.DoesNotExist:
            return Response(
                {"success": False, "info": "User not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # match the field sent by frontend against the token's phone number
        if field != identifier:
            return Response(
                {"success": False, "info": "field does not match the reset token"},
                status=status.HTTP_403_FORBIDDEN,
            )


        
        try:
            validate_password(password, user=user)
        except ValidationError as e:
            return Response(
                {"success": False, "info": e.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(password)
        user.password_changed = True
        user.login_enabled = True
        user.save(update_fields=["password", "password_changed", "login_enabled"])

        # invalidate token immediately — one time use
        cache.delete(f"reset_token:{identifier}")

        RefreshToken.objects.filter(user=user).update(is_revoked=True)

        return Response(
            {"success": True, "info": "password updated successfully"},
            status=status.HTTP_202_ACCEPTED,)
        
    reset_password.throttle_scope = "reset_password"
    



    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="forgot-password",
        throttle_classes=[ScopedRateThrottle],
    )
    def forgot_password(self, request, pk=None):
        data = request.data
        field = data.get("field")

        if not field:
            return Response(
                {
                    "success": False,
                    "info": "Field is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(Q(email=field) | Q(phone_number=field)).first()

        if not user:
            logger.warning(f"User with field {field} does not exist.")
            return Response(
                {"success": True, "info": f"OTP sent successfully to {field}"},
                status=status.HTTP_200_OK,
            )
        full_name = user.get_full_name() 
        try:
            if "@" in field:
                if not re.match(r"[^@]+@[^@]+\.[^@]+", field):
                    return Response(
                        {
                            "success": False,
                            "info": "Invalid email format.",
                        }
                    )
                
                auth.send_otp(email=field, full_name=full_name)

            elif re.match(r"^\+?\d{7,15}$", field):
                auth.send_otp(phone=field, full_name=full_name)
                

            else:
                return Response(
                    {
                        "success": False,
                        "info": "Invalid phone number format.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {"success": True, "info": f"OTP sent successfully to {field}"},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error sending OTP: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to send OTP.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    forgot_password.throttle_scope = "forgot_password"

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="verify-otp",
    )
    def verify_otp(self, request, pk=None):
        data = request.data
        field = data.get("field")
        otp = data.get("otp")

        if not field:
            return Response(
                {
                    "success": False,
                    "info": "Field is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not otp:
            return Response(
                {
                    "success": False,
                    "info": "OTP is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if "@" in field:
            verify = auth.verify_otp(email=field, user_entered_otp=otp)
        else:
            verify = auth.verify_otp(phone=field, user_entered_otp=otp)

        if verify:
            user = User.objects.filter(Q(email=field) | Q(phone_number=field)).first()
            temp_token = auth.generate_reset_token(user, identifier=field)
            return Response(
                {
                    "success": True,
                    "info": "OTP verified successfully.",
                    "temp_token": temp_token,
                },
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {
                    "success": False,
                    "info": "OTP verification failed. Please re-check your OTP and try again",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    def check_user_license_status(
        self, org_slug: str, email: str, phone: str = None
    ) -> bool:
        """Check if the user exists in any valid active license for the organization.

        Args:
            org_slug: The organization slug to check licenses for
            email: The user's email to verify against license users

        Returns:
            bool: True if the user exists in any valid active license, False otherwise
        """
        try:
            user = User.objects.filter(Q(email=email) | Q(phone_number=phone)).first()
            if not user:
                logger.warning(f"User with email {email} does not exist.")
                return False

            tenant = Tenant.objects.filter(org_slug=org_slug).first()
            if not tenant:
                logger.warning(f"Tenant with slug {org_slug} does not exist.")
                return False

            now = arrow.utcnow().datetime

            valid_license = (
                License.objects.filter(
                    tenant=tenant,
                    status=License.LicenseStatus.ACTIVE,
                    expiry_date__gte=now,
                )
                .order_by("-expiry_date")
                .first()
            )

            if valid_license:
                logger.warning(f"Valid license found until {valid_license.expiry_date}")

                if not valid_license.users.filter(pk=user.pk).exists():
                    logger.warning(f"{user} failed license check")
                    valid_license.users.add(user)
                    logger.info(f"User {user} added to license users")
                    return False

                logger.info(f"{user} passed license check")
                return True

            logger.info("No valid license found for user")
            return False

        except Exception as e:
            logger.error(f"Error checking user license status: {e}")
            return False

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="login",
        throttle_classes=[ScopedRateThrottle],
    )
    def login(self, request, pk=None):
        data = request.data
        field = data.get("field")
        password = data.get("password")

        if not field:
            return Response(
                {
                    "success": False,
                    "info": "Field is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not password:
            return Response(
                {
                    "success": False,
                    "info": "Password is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(Q(email=field) | Q(phone_number=field)).first()

        if not user:
            return Response(
                {"success": False, "info": "Invalid Email or Password"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = arrow.now().datetime

        if not user.role.name == "SUPER_ADMIN":
            if not user.password_changed and user.password_expiry <= now:
                logger.warning(
                    f"Blocked: expired password for {user.email}, expiry={user.password_expiry}, now={now}"
                )
                return Response(
                    {
                        "success": False,
                        "info": "Password expired. Contact your Administrator please",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            if not user.login_enabled:
                logger.warning(f"Blocked: login disabled for {user.email}")
                return Response(
                    {
                        "success": False,
                        "info": "Login disabled, Contact your Administrator please",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            license_status = self.check_user_license_status(
                user.org_slug, email=user.email, phone=user.phone_number
            )

            if not license_status and user.role.name != "SUPER_ADMIN":
                return Response(
                    {"success": False, "info": "Your organisation license is expired"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            # put this after you have created customers and staff
            # profile_picture = user.image.url if user.image else None
            # branch_id = None

            # if not profile_picture or not branch_id:
            #     member_instance = fetch_instances(
            #         Member, user.org_slug, {"phone_number": user.phone_number}
            #     )

            #     if member_instance:
            #         member = member_instance[0]

            #         if member.image:
            #             user.image = member.image
            #             user.save(update_fields=["image"])
            #             print("Profile picture found in Member:", profile_picture)

            #         if member.branch_id:
            #             try:
            #                 # fetch branch name safely from schema
            #                 branch_instance = fetch_instances(
            #                     Branch, user.org_slug, {"id": member.branch_id}
            #                 )
            #                 if branch_instance:
            #                     branch_id = branch_instance[0].id
            #             except ObjectDoesNotExist:
            #                 branch_id = None

            # if profile_picture:
            #     profile_picture = config("BASE_URL") + profile_picture
            # else:
            #     profile_picture = None

            if user.is_blocked:
                return Response(
                    {
                        "success": False,
                        "info": "Your account is blocked, Contact your admin please",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            checker = check_password(password, user.password)
            if checker:
                user.last_login = now
                user.save(update_fields=["last_login"])

                access_token = auth.generate_access_token(user)
                refresh_token = auth.generate_refresh_token(user)

                if user.password_changed:
                    return Response(
                        {
                            "success": True,
                            "info": "User logged in successfully",
                            "password_changed": True,
                            "access_token": access_token,
                            "refresh_token": refresh_token,
                            "role": user.role.name,
                            # "branch_id": branch_id,
                            # "profile_picture": profile_picture
                        },
                        status=status.HTTP_200_OK,
                    )

                else:
                    return Response(
                        {
                            "success": True,
                            "info": "User logged in successfully",
                            "password_changed": False,
                            "access_token": access_token,
                            "refresh_token": refresh_token,
                        },
                        status=status.HTTP_200_OK,
                    )

            else:
                return Response(
                    {"success": False, "info": "Invalid Email or Password"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # profile_picture = user.image.url if user.image else None
        # if not profile_picture:
        #     member_instance = fetch_instances(
        #         Member, user.org_slug, {"email": user.email}
        #     )

        #     if member_instance:
        #         member = member_instance[0]

        #         if member.image:
        #             user.image = member.image
        #             user.save(update_fields=["image"])
        #             print("Profile picture found in Member:", profile_picture)

        #     if profile_picture:
        #         profile_picture = config("BASE_URL") + profile_picture
        #     else:
        #         profile_picture = None

        checker = check_password(password, user.password)
        if checker:
            user.last_login = now
            user.save(update_fields=["last_login"])
            access_token = auth.generate_access_token(user)
            refresh_token = auth.generate_refresh_token(user)

            if user.password_changed:
                return Response(
                    {
                        "success": True,
                        "info": "User logged in successfully",
                        "password_changed": True,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "role": user.role.name,
                        # "profile_picture" : profile_picture
                    },
                    status=status.HTTP_200_OK,
                )

            else:
                return Response(
                    {
                        "success": True,
                        "info": "User logged in successfully",
                        "password_changed": False,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                    },
                    status=status.HTTP_200_OK,
                )

        return Response(
            {
                "success": False,
                "info": "Invalid Email or Password",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    login.throttle_scope = "login"

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="passwordless-login",
        throttle_classes=[ScopedRateThrottle],
    )
    def login_without_password(self, request, *args, **kwargs):
        try:
            data = request.data

            field = data.get("field")

            if not field:
                return Response(
                    {"success": False, "info": "Field is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = User.objects.filter(Q(email=field) | Q(phone_number=field)).first()

            if not user:
                return Response(
                    {
                        "success": True,
                        "info": f"OTP sent successfully to {field}",
                    },
                    status=status.HTTP_200_OK,
                )
            full_name = user.get_full_name() 
            license_status = self.check_user_license_status(
                user.org_slug, email=user.email, phone=user.phone_number
            )

            if not license_status and user.role.name != "SUPER_ADMIN":
                logger.warning("license for user not found or expired")
                return Response(
                    {
                        "success": True,
                        "info": f"OTP sent successfully to {field}",
                    },
                    status=status.HTTP_200_OK,
                )

            if "@" in field:
                if not re.match(r"[^@]+@[^@]+\.[^@]+", field):
                    return Response(
                        {"success": False, "info": "Invalid Email or Phone Number"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                auth.send_otp(email=field, full_name=full_name)

            elif re.match(r"^\+?\d{7,15}$", field):
                auth.send_otp(phone=field, full_name=full_name)

            else:
                return Response(
                    {"success": False, "info": "Invalid email or phone number format"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "success": True,
                    "info": f"OTP sent successfully to {field}, If user exists",
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error sending OTP: {e}")
            return Response(
                {"success": False, "info": "Failed to send OTP."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    login_without_password.throttle_scope = "login_without_password"

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="passwordless-login-verify-otp",
    )
    def verify_passwordless_otp(self, request, *args, **kwargs):
        try:
            data = request.data
            field = data.get("field")
            otp = data.get("otp")

            if not field:
                return Response(
                    {
                        "success": False,
                        "info": "Field is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not otp:
                return Response(
                    {
                        "success": False,
                        "info": "OTP is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = User.objects.filter(Q(email=field) | Q(phone_number=field)).first()

            if not user:
                return Response(
                    {
                        "success": False,
                        "info": "Invalid Email or Phone Number",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            
            if "@" in field:
                verify = auth.verify_otp(email=field, user_entered_otp=otp)
            else:
                verify = auth.verify_otp(phone=field, user_entered_otp=otp)            

            if verify:
                access_token = auth.generate_access_token(user)
                refresh_token = auth.generate_refresh_token(user)

                if user.password_changed:
                    return Response(
                        {
                            "success": True,
                            "info": "User logged in successfully",
                            "password_changed": True,
                            "access_token": access_token,
                            "refresh_token": refresh_token,
                            "role": user.role.name,
                        },
                        status=status.HTTP_200_OK,
                    )

                else:
                    return Response(
                        {
                            "success": False,
                            "info": "Kindly change password to continue",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            else:
                return Response(
                    {
                        "success": False,
                        "info": "OTP verification failed. Please re-check your OTP and try again",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except Exception as e:
            logger.error(f"Error verifying OTP: {e}")
            return Response(
                {"success": False, "info": "Failed to verify OTP."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["get"],
        content_model=User,
        permission_classes=[CustomPermission],
        url_path="users-by-organization/(?P<uid>[^/.]+)",
    )
    def users_by_organization(self, request, uid=None, *args, **kwargs):
        try:
            if not uid:
                return Response(
                    {
                        "success": False,
                        "info": "Organization UID is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            tenant = Tenant.objects.filter(org_slug=uid).first()

            if not tenant:
                return Response(
                    {
                        "success": False,
                        "info": "Organization does not exist.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            users = User.objects.filter(tenant=tenant)
            serializer = UserListSerializer(users, many=True)
            return Response(
                {
                    "success": True,
                    "info": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error fetching users by organization: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to fetch users by organization.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["get"],
        url_path="license-by-organization/(?P<uid>[^/.]+)",
    )
    def license_by_organization(self, request, uid=None, *args, **kwargs):
        try:
            if not uid:
                return Response(
                    {
                        "success": False,
                        "info": "Organization UID is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            tenant = Tenant.objects.filter(org_slug=uid).first()

            if not tenant:
                return Response(
                    {
                        "success": False,
                        "info": "Organization does not exist.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            license = License.objects.filter(tenant=tenant).first()

            if not license:
                return Response(
                    {
                        "success": False,
                        "info": "License does not exist.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            serializer = LicenseListSerializer(license, many=True)

            return Response(
                {
                    "success": True,
                    "info": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error fetching license by organisation {str(e)}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to fetch license by organisation.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        url_path="send-reset-password",
    )
    def send_reset_password(self, request, *args, **kwargs):
        try:
            data = request.data
            field = data.get("field")

            if not field:
                return Response(
                    {
                        "success": False,
                        "info": "Field is required.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            user = User.objects.filter(Q(email=field) | Q(phone_number=field)).first()
            

            if not user:
                logger.warning(f"User with field {field} does not exist.")
                return Response(
                    {"success": True, "info": "Password reset successful."},
                    status=status.HTTP_200_OK,
                )

            new_password = "".join(
                random.choices(string.ascii_letters + string.digits, k=12)
            )
            full_name = user.get_full_name() 
            user.set_password(new_password)
            user.password_expiry = arrow.now().shift(days=+3).datetime
            user.save(update_fields=["password", "password_expiry",])
            auth.send_reset_password(field=field, password=new_password, full_name=full_name)

            return Response(
                {"success": True, "info": "Password reset successful."},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error sending reset password: {e}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to send reset password.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[permissions.AllowAny],
        url_path="refresh-token",
        throttle_classes=[ScopedRateThrottle],
    )
    def refresh_token(self, request):

        refresh_token = request.data.get("refresh_token")

        if not refresh_token:
            return Response(
                {
                    "success": False,
                    "info": "Refresh token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            payload = jwt.decode(
                refresh_token,
                config("SECRET_KEY"),
                algorithms=["HS256"],
            )

            refresh_jti = payload.get("jti")

            # Redis blacklist check
            if cache.get(f"blacklist:refresh:{refresh_jti}"):
                return Response(
                    {
                        "success": False,
                        "info": "Refresh token has been blacklisted. Please login again.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if payload.get("type") != "refresh":
                return Response(
                    {
                        "success": False,
                        "info": "Invalid token type.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            token_obj = RefreshToken.objects.filter(
                jti=refresh_jti, is_revoked=False
            ).first()
            
            if not token_obj:
                return Response(
                    {"success": False, "info": "Refresh token not found or already revoked"},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
                
            if token_obj.is_expired():
                return Response(
                    {
                        "success": False,
                        "info": "Refresh token has expired. Please login again.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            token_obj.last_used_at = arrow.utcnow().datetime
            token_obj.save(update_fields=["last_used_at"])

            access_token = auth.generate_access_token(token_obj.user)

            return Response(
                {
                    "success": True,
                    "info": "Token refreshed successfully.",
                    "access_token": access_token,
                },
                status=status.HTTP_200_OK,
            )

        except (
            jwt.ExpiredSignatureError,
            jwt.InvalidTokenError,
            RefreshToken.DoesNotExist,
        ) as e:
            logger.error(f"Error refreshing token: {str(e)}")
            return Response(
                {"success": False, "info": "Invalid or expired refresh token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

    refresh_token.throttle_scope = "refresh_token"

    @action(detail=False, methods=["post"], url_path="logout")
    def logout(self, request):
        refresh_token = request.data.get("refresh_token")

        if refresh_token:
            try:
                payload = jwt.decode(
                    refresh_token,
                    config("SECRET_KEY"),
                    algorithms=["HS256"],
                    options={"verify_exp": False},
                )

                refresh_jti = payload.get("jti")
                exp = payload.get("exp")

                if refresh_jti and exp:
                    ttl = exp - int(arrow.utcnow().timestamp())

                    if ttl > 0:
                        cache.set(
                            f"blacklist:refresh:{refresh_jti}",
                            "blacklisted",  # ← value
                            timeout=ttl,    # ← timeout
                        )

                RefreshToken.objects.filter(jti=refresh_jti).update(is_revoked=True)

            except jwt.InvalidTokenError:
                logger.error("Invalid refresh token provided for logout.")
                pass

        else:
            logger.error("Refresh token not provided for logout.")
            return Response(
                {
                    "success": False,
                    "info": " Refresh token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # blacklist the access token in Redis
        auth_header = request.headers.get("Authorization")

        if auth_header:
            try:
                prefix, token = auth_header.split(" ")

                if prefix.lower() != "bearer":
                    return Response(
                        {
                            "success": False,
                            "info": "Invalid token prefix.",
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                payload = jwt.decode(
                    token,
                    config("SECRET_KEY"),
                    algorithms=["HS256"],
                    options={"verify_exp": False},
                )

                jti = payload.get("jti")
                exp = payload.get("exp")

                if jti and exp:
                    ttl = exp - int(arrow.utcnow().timestamp())

                    if ttl > 0:
                        cache.set(f"blacklist:access:{jti}", "blacklisted", timeout=ttl)
            except Exception as e:
                print(f"Error blacklisting access token: {str(e)}")
                pass

        return Response(
            {
                "success": True,
                "info": "Logged out successfully.",
            },
            status=status.HTTP_200_OK,
        )


class FetchOrgUsers(viewsets.ModelViewSet):
    content_model = User
    permission_classes = [CustomPermission]
    serializer_class = UserListSerializer
    pagination_class = FetchDataPagination

    def get_queryset(self):
        return User.objects.filter(org_slug=self.request.user.org_slug).order_by("id")

    def list(self, request, *args, **kwargs):
        try:
            role = request.query_params.get("role")
            user_status=request.query_params.get("status")
            name = request.query_params.get("name")
            
            users = User.objects.filter(org_slug=self.request.user.org_slug).order_by(
                "id"
            )
            
            if role:
                users = users.filter(role__name=role)
            if user_status:
                users = users.filter(status=user_status)
            if name:
                users =users.filter(
                    Q(first_name__icontains=name) |
                    Q(last_name__icontains=name) |
                    Q(middle_name__icontains=name) 
                )
            page = self.paginate_queryset(users)

            if page is not None:
                serializer = self.get_serializer(page, many=True)
                data = self.get_paginated_response(serializer.data).data
                
                return Response({
                    "success": True,
                    "info": data["results"],
                    "meta": {
                        "count": data["count"],
                        "next": data["next"],
                        "previous": data["previous"],
                    }
                }, status=status.HTTP_200_OK)

            serializer = self.get_serializer(users, many=True)
            return Response({"success": True, "info": serializer.data}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error fetching org users: {str(e)}", exc_info=True)

            return Response(
                {
                    "success": False,
                    "info": "Failed to fetch organisation users.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class FetchOrgUserGroups(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint to fetch all user groups belonging to the requesting user's organization
    """

    content_model = UserGroup
    permission_classes = [CustomPermission]
    serializer_class = UserGroupListSerializer

    def list(self, request, *args, **kwargs):
        try:
            user_groups = (
                UserGroup.objects.filter(tenant=request.user.tenant)
                .all()
                .order_by("-created_at")
            )

            serializer = self.serializer_class(user_groups, many=True)

            return Response(
                {
                    "success": True,
                    "info": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Error fetching org user groups: {str(e)}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to fetch organisation user groups.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class SystemLogsViewset(viewsets.ViewSet):
    """
    Endpoints for system-level utilities like log downloads.
    """

    @extend_schema(
        summary="Download debug log file",
        description="Downloads the debug.log file. Only accessible by super admins.",
        responses={
            200: OpenApiResponse(
                description="Debug log file",
                # This indicates a file download response
            ),
            403: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Forbidden - Not a super admin",
                examples=[
                    OpenApiExample(
                        "Forbidden Response",
                        value={
                            "success": False,
                            "info": "Hate to be that way, but you ain't allowed here big dawg!!",
                        },
                    )
                ],
            ),
            404: OpenApiResponse(description="Debug log file not found"),
            500: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Server error",
                examples=[
                    OpenApiExample(
                        "Error Response",
                        value={
                            "success": False,
                            "info": "Failed to download debug log.",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="download-debug-log")
    def download_debug_log(self, request):
        try:
            if request.user.role.name != "SUPER_ADMIN":
                return Response(
                    {
                        "success": False,
                        "info": "Hate to be that way, but you ain't allowed here big dawg!!",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            if not request.user.is_superuser:
                return Response(
                    {
                        "success": False,
                        "info": "Are you supposed to be here?!, Exactly! Exit is that way chale!",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            base_dir = settings.BASE_DIR
            log_path = os.path.join(base_dir, "debug.log")

            if not os.path.exists(log_path):
                raise Http404("debug.log file not found")

            response = FileResponse(
                open(log_path, "rb"),
                as_attachment=True,
                filename="debug.log",
                content_type="text/plain",
            )

            return response

        except Exception as e:
            logger.error(f"Error downloading debug log: {str(e)}")
            return Response(
                {
                    "success": False,
                    "info": "Failed to download debug log.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

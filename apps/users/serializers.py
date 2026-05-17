from rest_framework import serializers
from apps.users.models import User, UserGroup, UserRole


class UserRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRole
        fields = ["id", "uid", "name", "created_at", "updated_at"]


class UserGroupCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserGroup
        fields =  [
            "id",
            "uid",
            "name",
            "description",
            "users",
            "permissions",
            "tenant",
            "is_global",
            "created_at",
            "updated_at",
        ]


class UserGroupListSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()
    users = serializers.SerializerMethodField()
    tenants = serializers.SerializerMethodField()

    def get_permissions(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        perms = obj.permissions.select_related("content_type")

        # Super admin sees everything
        if user and user.role.name == "SUPER_ADMIN":
            return [perm.codename for perm in perms]

        # Non-super-admin: hide client/license permissions
        filtered_perms = [
            perm.codename for perm in perms if perm.content_type.app_label != "clients"
        ]

        return filtered_perms

    def get_users(self, obj):
        # Reverse lookup: Get all users that belong to this group
        return [
            {
                "id": user.id,
                "uid": user.uid,
                "full_name": f"{user.first_name} {user.last_name}",
            }
            for user in obj.users.all()
        ]

    def get_tenants(self, obj):
        if obj.tenant:
            return [
                {
                    "id": tenant.id,
                    "uid": tenant.uid,
                    "name": tenant.name,
                }
                for tenant in obj.tenant.all()
            ]
        else:
            return None

    class Meta:
        model = UserGroup
        fields = [
            "id",
            "uid",
            "name",
            "description",
            "permissions",
            "users",
            "tenants",
            "created_at",
            "updated_at",
        ]


class UserCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "email",
            "phone_number",
            "gender",
            "image",
            "role",
            "status",
            "is_blocked",
            "login_enabled",
            "org_slug",
            "tenant",
            "password",
            "password_changed",
            "password_expiry",
        ]


class UserSelfUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "middle_name",
            "phone_number",
            "gender",
            "image",
        ]


class UserAdminUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "role",
            "status",
            "login_enabled",
            "is_blocked",
        ]


class UserListSerializer(serializers.ModelSerializer):
    tenant = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()
    groups = serializers.SerializerMethodField()

    def get_tenant(self, obj):
        if obj.tenant:
            return [
                {
                    "id": obj.tenant.id,
                    "uid": obj.tenant.uid,
                    "name": obj.tenant.name,
                }
            ]
        else:
            return None

    def get_role(self, obj):
        if obj.role:
            return [
                {
                    "id": obj.role.id,
                    "uid": obj.role.uid,
                    "name": obj.role.name,
                }
            ]
        else:
            return None

    def get_groups(self, obj):
        user_groups = UserGroup.objects.filter(users=obj)

        if user_groups.exists():
            return [
                {
                    "id": group.id,
                    "uid": group.uid,
                    "name": group.name,
                    "permissions": [perm.codename for perm in group.permissions.all()],
                }
                for group in user_groups
            ]
        else:
            return None

    class Meta:
        model = User
        fields = [
            "id",
            "uid",
            "first_name",
            "last_name",
            "middle_name",
            "email",
            "image",
            "phone_number",
            "role",
            "tenant",
            "groups",
            "status",
            "gender",
            "last_login",
            "is_blocked",
            "created_at",
        ]

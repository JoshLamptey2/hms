import logging
import jwt
import arrow
import uuid
import re
import random
from rest_framework.response import Response
from rest_framework.authentication import BaseAuthentication
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from apps.client.models import RefreshToken
from decouple import config
from django.core.cache import cache
from apps.users.utils import send_notification
from apps.notifications.service import NotificationService

service = NotificationService()

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()
# notify = Notify()


class Authenticator:

    def generate_access_token(self, user):
        jti = uuid.uuid4()

        payload = {
            "jti": str(jti),
            "user_id": user.id,
            "user_uid": str(user.uid),
            "full_name": f"{user.first_name} {user.last_name}",
            "type": "access",
            "iat": arrow.utcnow().datetime,
            "exp": arrow.utcnow().shift(minutes=+15).datetime,
        }

        token = jwt.encode(payload, config("SECRET_KEY"), algorithm="HS256")

        return token

    def generate_refresh_token(self, user):
        jti = uuid.uuid4()

        payload = {
            "jti": str(jti),
            "user_id": user.id,
            "user_uid": str(user.uid),
            "full_name": f"{user.first_name} {user.last_name}",
            "type": "refresh",
            "iat": arrow.utcnow().datetime,
            "exp": arrow.utcnow().shift(hours=+12).datetime,
        }
        token = jwt.encode(payload, config("SECRET_KEY"), algorithm="HS256")
        RefreshToken.objects.create(
            jti=jti,
            user=user,
            token=token,
            expires_at=arrow.utcnow().shift(days=+7).datetime,
        )
        return token
    
    def generate_reset_token(self, user, identifier):
        payload = {
            "user_id": user.id,
            "user_uid": str(user.uid),
            "identifier": identifier,
            "type": "password_reset",
            "iat": arrow.utcnow().datetime,
            "exp": arrow.utcnow().shift(minutes=10).datetime,
        }
        token = jwt.encode(payload, config("SECRET_KEY"), algorithm="HS256")
        
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        
        cache.set(f"reset_token:{identifier}", token, timeout=600)
        return token
    
    def generate_otp(self):
        rand = random.randint(100000, 999999)
        return rand

    def send_otp(self, email=None, phone=None, full_name=None):
        try:
            otp = self.generate_otp()
            logger.info(f"otp generated: {otp}")

            if email:
                cache.set(email, otp, timeout=600)
                
                service.send_otp(
                    email_address=email,
                    otp=otp,
                    expires_in_minutes=10,
                    full_name=full_name,
                )
                
                logger.info(f"OTP email sent to {email}")

            elif phone:
                cache.set(phone, otp, timeout=300)
                service.send_otp(
                    phone_number=phone,
                    otp=otp,
                    expires_in_minutes=10,
                    full_name=full_name
                )
                logger.info(f"OTP sms sent to {phone}")

            else:
                raise 

            return otp

        except Exception as e:
            logger.error(f"Error sending OTP: {e}")
            raise {
                "success": False,
                "info": "Failed to send OTP.",
            }

    def verify_otp(self, user_entered_otp, email=None, phone=None):
        cache_key = email if email else phone
        stored_otp = cache.get(cache_key)
        logger.info(stored_otp)

        if stored_otp is None:
            logger.warning("OTP has expired or does not exist.")
            return False

        try:
            user_entered_otp = int(user_entered_otp)
        except ValueError:
            logger.warning("Invalid OTP format provided by user.")
            return False

        if stored_otp != user_entered_otp:
            logger.warning("Wrong OTP provided by user.")
            return False
        else:
            cache.delete(cache_key)
            logger.info("OTP verified successfully.")
            return True

    def forget_verify_otp(self, user_entered_otp, email=None, phone=None):
        cache_key = email if email else phone

        if not cache_key:
            return Response(
                {
                    "success": False,
                    "info": "Email or phone must be provided.",
                }
            )

        stored_otp = cache.get(cache_key)
        logger.info(stored_otp)

        if stored_otp is None:
            logger.warning("OTP has expired or does not exist.")
            return Response(
                {
                    "success": False,
                    "info": "OTP has expired or does not exist. Kindly click on the resend button to continue.",
                }
            )

        try:
            user_otp = int(user_entered_otp)
        except ValueError:
            return Response(
                {
                    "success": False,
                    "info": "Invalid OTP format provided by user.",
                }
            )

        if stored_otp != user_otp:
            retries = cache.get(f"retries:{email}")
            if retries is None:
                retries = 0
            retries += 1
            cache.set(f"retries:{email}", retries, timeout=300)
            if retries >= 4:
                cache.delete(email)
                return Response(
                    {
                        "success": False,
                        "info": "Too many failed attempts. Please try resending the OTP.",
                    }
                )
            return Response(
                {
                    "success": False,
                    "info": f"Wrong OTP provided. You have {3 - retries} attempts left.",
                }
            )

        cache.delete(f"{email}")
        cache.delete(f"retries:{email}")
        logger.info("OTP verified successfully.")
        return Response(
            {
                "success": True,
                "info": "OTP verified successfully.",
            }
        )

    def send_reset_password(self, field, password=None, full_name=None):
        try:
            if "@" in field:
                if not re.match(r"[^@]+@[^@]+\.[^@]+", field):
                    return Response(
                        {
                            "success": False,
                            "info": "Invalid email format.",
                        }
                    )

                service.send_temporary_password(
                    email_address=field,
                    password=password,
                    full_name=full_name,
                )
                logger.info(f"Reset password email sent to {field}")

            else:
                if not re.match(r"^\+?\d{7,15}$", field):
                    return Response(
                        {
                            "success": False,
                            "info": "Invalid phone number format.",
                        }
                    )

                service.send_temporary_password(
                    phone_number=field,
                    password=password,
                    full_name=full_name
                )
                logger.info(f"Reset password sms sent to {field}")

        except Exception as e:
            logger.error(f"Error sending reset Password: {e}")
            raise e


class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return None

        try:
            prefix, token = auth_header.split(" ")
            if prefix.lower() != "bearer":
                raise AuthenticationFailed("invalid token prefix")
        except ValueError:
            raise AuthenticationFailed("Invalid authorization header")

        try:
            payload = jwt.decode(
                token,
                config("SECRET_KEY"),
                algorithms=["HS256"],
            )

            jti = payload.get("jti")
            if cache.get(f"blacklist:access:{jti}"):
                raise PermissionDenied("Access token has been revoked")

            if payload.get("type") != "access":
                raise AuthenticationFailed("Invalid token type")

            try:
                logged_user = User.objects.get(
                    id=payload["user_id"], uid=payload["user_uid"]
                )
            except User.DoesNotExist:
                raise AuthenticationFailed("User not found")

            if logged_user.is_blocked:
                raise AuthenticationFailed(
                    "Sorry, Your account has been blocked.\n Please contact your admin."
                )

            cache.set(f"org_slug:{logged_user.id}", logged_user.org_slug)

            return (logged_user, payload)

        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("Access token expired")
        except jwt.InvalidTokenError:
            raise AuthenticationFailed("Invalid token")


class JWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.users.auth.JWTAuthentication"
    name = "JWTAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }

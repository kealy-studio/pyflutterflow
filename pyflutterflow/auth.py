import os
from pydantic import BaseModel
from fastapi import HTTPException, Depends
from fastapi import status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin.auth import ExpiredIdTokenError
from firebase_admin import auth
from pyflutterflow import PyFlutterflow, constants
from pyflutterflow.database.supabase.supabase_client import SupabaseClient
from pyflutterflow.services.email.resend_service import ResendService
from pyflutterflow.database.firestore.firestore_client import FirestoreClient
from pyflutterflow.utils import trigger_slack_webhook
from pyflutterflow.logs import get_logger

logger = get_logger(__name__)
security = HTTPBearer()

AVATAR_PLACEHOLDER_URL = os.getenv("AVATAR_PLACEHOLDER_URL", "")


class FirestoreUser(BaseModel):
    """
    This will be the structure of the user object stored in firestore.
    """
    uid: str
    email: str = constants.GUEST_EMAIL
    display_name: str = 'Unnamed'
    photo_url: str = AVATAR_PLACEHOLDER_URL
    is_admin: bool = False


class FirebaseUser(BaseModel):
    """
    When a firebase auth token is validated and decoded, this
    is the structure of the user data returned.
    """
    uid: str
    email_verified: bool = False
    email: str = constants.GUEST_EMAIL
    picture: str = AVATAR_PLACEHOLDER_URL
    name: str = 'Guest'
    auth_time: int
    iat: int
    exp: int
    role: str = "user"


class FirebaseAuthUser(BaseModel):
    """
    The user structure from the firebase auth Python SDK differs slightly from
    FirebaseUser, so this model is used to represent that user object.
    """
    uid: str
    email: str = constants.GUEST_EMAIL
    display_name: str | None = None
    photo_url: str | None = None
    last_login_at: str
    created_at: str
    custom_attributes: str | None = None


class FirebaseUserClaims(BaseModel):
    """
    Some tokens have custom claims, and this feature is used by pyflutterflow
    to assign and manage admin rights at the token level.
    """
    uid: str
    role: str = 'admin'


async def get_admin_user(token: HTTPAuthorizationCredentials = Depends(security)) -> FirebaseUser:
    """Verify the JWT token, check for the admin service role, and then return the user object."""
    current_user = await get_current_user(token)
    if current_user.role == constants.ADMIN_ROLE:
        return current_user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="You are not an admin.")


async def get_current_user(token: HTTPAuthorizationCredentials = Depends(security)) -> FirebaseUser:
    """Verify the JWT token and return the user object."""
    settings = PyFlutterflow().get_settings()
    try:
        decoded_token = auth.verify_id_token(token.credentials)
        if settings.require_verified_email and not decoded_token.get("email_verified"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email not verified")
        user = FirebaseUser(**decoded_token)
        return user
    except ExpiredIdTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Auth token has expired")
    except Exception as e:
        logger.error("Error encountered during JWT token verification: %s", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


async def get_users_list(_: FirebaseUser = Depends(get_admin_user)) -> list[FirebaseAuthUser]:
    """Get a list of all users in the firebase auth system."""
    try:
        users = auth.list_users(max_results=500)
        users_list = []
        for user in users.iterate_all():
            data = user._data
            users_list.append(FirebaseAuthUser(
                uid=data.get('localId'),
                email=data.get('email') or constants.GUEST_EMAIL,
                display_name=data.get('displayName') or 'Unnamed',
                photo_url=data.get('photoUrl'),
                last_login_at=data.get('lastLoginAt'),
                created_at=data.get('createdAt'),
                custom_attributes=data.get('customAttributes'),
            ))
        return users_list
    except Exception as e:
        logger.error("Error encountered during getting users list: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Error encountered while getting users list.')


async def get_firebase_user_by_uid(user_uid: str, _: FirebaseUser = Depends(get_admin_user)) -> FirebaseAuthUser:
    """Get a list of all users in the firebase auth system."""
    try:
        user = auth.get_user(uid=user_uid)
        data = user._data
        return FirebaseAuthUser(
            uid=data.get('localId'),
            email=data.get('email'),
            display_name=data.get('displayName'),
            photo_url=data.get('photoUrl'),
            last_login_at=data.get('lastLoginAt'),
            created_at=data.get('createdAt'),
            custom_attributes=data.get('customAttributes'),
        )
    except Exception as e:
        logger.error("Error encountered during getting users list: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Error encountered while getting users list.')


async def run_supabase_firestore_user_sync(_: FirebaseUser = Depends(get_admin_user)) -> None:
    """
    Run a sync of Firebase users with Supabase users. Note this function
    would need to be upgraded to handle large user lists (>1000 users).
    """
    sb_client = await SupabaseClient().get_client()
    settings = PyFlutterflow().get_settings()
    users_table = settings.users_table or 'users'
    logger.info("Running user sync between Firebase and Supabase.")
    response = await sb_client.table(users_table).select('id').execute()
    supabase_users = [user['id'] for user in response.data]
    firestore_client = FirestoreClient().get_client()
    user_col = firestore_client.collection("users")
    try:
        async for userdoc in user_col.stream():
            if userdoc.id not in supabase_users:
                user = userdoc.to_dict()
                logger.info("Adding user: %s", userdoc.id)
                if user.get('display_name') and user.get('email'):
                    await sb_client.table(users_table).insert({
                        'id': userdoc.id,
                        'email': user.get('email'),
                        'display_name': user.get('display_name'),
                        'photo_url': user.get('photo_url') or settings.avatar_placeholder_url or ''
                    }).execute()
                else:
                    logger.error("User %s does not have a display name or email.", userdoc.id)
    except Exception as e:
        trigger_slack_webhook(f"Error encountered during user sync: {e}")
        logger.error("Error encountered during getting users list: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f'Error encountered while getting users list: {e}')


async def onboard_new_user(current_user: FirebaseUser = Depends(get_current_user)):
    """Create a new user record in Supabase and send a welcome email to the user."""
    settings = PyFlutterflow().get_settings()
    users_table = settings.users_table or 'users'
    sb_client = await SupabaseClient().get_client()
    firestore_client = FirestoreClient().get_client()
    doc = await firestore_client.collection('users').document(current_user.uid).get()
    user_data = doc.to_dict()
    try:
        response = await sb_client.table(users_table).upsert({
            'id': current_user.uid,
            'email': current_user.email,
            'display_name': user_data.get('display_name', current_user.name),
            'photo_url': user_data.get('photo_url') or current_user.picture or settings.avatar_placeholder_url,
        }).execute()
        if not response.data or len(response.data) != 1:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Error encountered while creating user record in supabase: Incorrect Postgrest response.'
            )
        if current_user.email != constants.GUEST_EMAIL:
            verification_link = auth.generate_email_verification_link(current_user.email) if not current_user.email_verified else None
            await ResendService().send_welcome_email(current_user, verification_link)
        logger.info("User record created in supabase for user: %s", current_user.uid)
    except Exception as e:
        trigger_slack_webhook(f"Error encountered while creating user record in supabase: {e}")
        logger.error("Error encountered while creating user record in supabase: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f'Error encountered creating user record in supabase: {e}')


async def set_admin_flag(user_id: str, is_admin: bool):
    firestore_client = FirestoreClient().get_client()
    doc_ref = firestore_client.collection('users').document(user_id)
    await doc_ref.update({
        'is_admin': is_admin
    })


async def set_user_role(user_claim: FirebaseUserClaims, user: FirebaseUser = Depends(get_admin_user)) -> FirebaseUser:
    """Update the service role permissions on the desired firebase user account. Take care: this action can create an admin."""
    if user.role != constants.ADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User does not have permission to set user role.")
    try:
        logger.info("Setting user role: %s for user: %s", user_claim.role, user_claim.uid)
        auth.set_custom_user_claims(user_claim.uid, {'role': user_claim.role})
    except Exception as e:
        logger.error("Error encountered during setting user role: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Error encountered while setting user role.')
    await set_admin_flag(user_claim.uid, is_admin=user_claim.role==constants.ADMIN_ROLE)
    return user


async def generate_firebase_verify_link(email: str) -> str:
    return auth.generate_email_verification_link(email)


async def delete_user(user_uid: str, user: FirebaseUser = Depends(get_admin_user)):
    """Delete a user from the firebase auth system."""
    if user.role != constants.ADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User delete denied: Admin check failed.")

    # From Firebase Auth
    try:
        auth.delete_user(user_uid)
        logger.info("Deleted user: %s", user_uid)
    except Exception as e:
        logger.error("Error encountered during deleting user: %s", e)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Error encountered while deleting user.')

    # From Firestore
    try:
        firestore_client = FirestoreClient().get_client()
        user_doc_ref = firestore_client.collection('users').document(user_uid)
        user_doc_ref.delete()
        logger.info("Deleted Firestore data for user %s", user_uid)
    except Exception as e:
        logger.error("Error deleting Firestore data for user %s: %s", user_uid, e)

    # From Supabase
    try:
        client = await SupabaseClient().get_client()
        settings = PyFlutterflow().get_settings()
        users_table = settings.users_table or 'users'
        await client.table(users_table).delete().eq('id', user_uid).execute()
        logger.info("Deleted Supabase data for user %s", user_uid)
    except Exception as e:
        logger.error("Error deleting Supabase data for user %s: %s", user_uid, e)

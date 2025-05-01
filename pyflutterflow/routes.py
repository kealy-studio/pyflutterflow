from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import HTMLResponse
from starlette.responses import FileResponse
from pyflutterflow.logs import get_logger
from pyflutterflow import PyFlutterflow
from pyflutterflow.auth import (set_user_role, get_users_list, get_current_user, get_firebase_user_by_uid, delete_user,
                                FirebaseUser, FirebaseAuthUser, run_supabase_firestore_user_sync, onboard_new_user)
from pyflutterflow.database.supabase.supabase_functions import proxy, proxy_with_body
from pyflutterflow.services.cloudinary_service import CloudinaryService
from pyflutterflow import constants
from pyflutterflow.webpages.routes import webpages_router
from pyflutterflow.services.notifications.routes import notifications_router

logger = get_logger(__name__)

router = APIRouter(
    prefix='',
    tags=['Pyflutterflow internal routes'],
)

router.include_router(webpages_router)
router.include_router(notifications_router)


@router.get("/", response_class=HTMLResponse)
async def root():
    html_content = """
    <html>
        <body>
            <h1>The Python API</h1>
            <p>This is the API root. Visit <a href="/docs">/docs</a> to see the available
            endpoints, or <a href="/dashboard">/dashboard</a> to go to the admin portal.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


@router.get("/configure")
async def serve_vue_config():
    """
    This route serves configuration for the Vue.js admin dashboard.

    The dashboard consumes this configuration to connect to the Firebase and Supabase APIs,
    and to handle the correct database schema.
    """
    settings = PyFlutterflow().get_settings()
    file_path = "admin_config.dev.json" if settings.environment == constants.DEV_ENVIRONMENT else "admin_config.json"
    return FileResponse(file_path)


########### Firebase auth routes ##############


@router.post("/admin/auth/set-role")
async def set_role(_: FirebaseUser = Depends(set_user_role)) -> None:
    """
    Set a role (e.g. admin) for a firebase auth user. This will create a custom
    claim in the user's token, available in all requests.

    Also sets a flag called 'is_admin' in the firebase users table.
    """
    pass


@router.get("/admin/auth/users", response_model=list[FirebaseAuthUser])
async def get_users(users: list = Depends(get_users_list)):
    """
    Get a list of all Firebase users. This route is only accessible to admins.
    """
    # TODO users pagination
    return users


@router.get("/admin/auth/users/{user_uid}", response_model=FirebaseAuthUser)
async def get_user_by_id(users: list = Depends(get_firebase_user_by_uid)):
    """
    Get a Firebase user by their UID. This route is only accessible to admins.
    """
    return users


@router.post("/admin/auth/delete-user/{user_uid}", dependencies=[Depends(delete_user)])
async def admin_user_delete() -> None:
    pass



########### User routes ##################

@router.post("/admin/auth/sync-users", dependencies=[Depends(run_supabase_firestore_user_sync)])
async def supabase_firestore_user_sync() -> dict:
    """
    Sync Firebase users with Supabase users. This will create a new user in the
    Supabase users table for each Firebase user, if not present.
    """
    return {
        "message": "Successfully synced Firebase users with Supabase users"
    }


@router.post("/auth/onboard-user", dependencies=[Depends(onboard_new_user)])
async def onboard_user() -> None:
    pass


###############################################




########### SUPABASE PROXY ROUTES ##############
#
# These routes are used to proxy requests to the Supabase API.
#
# You can use them just as you would use the Supabase REST API
# (i.e. the postgrest API). However, the proxy serves to overcome
# one of Flutterflow's main limitations: the inability to use
# Firebase auth tokens in the Supabase API. To achieve this, these
# routes will mint a new supabase JWT token based on the Firebase
# one, including admin privilages. Minted admin tokens will have
# an embedded 'user_role'='admin' claim, which can be used in RLS
# to authenticate admin requests.
#
@router.get("/supabase/{path:path}")
async def supabase_get_proxy(response = Depends(proxy)):
    """Proxy function to handle forwarding a request to Supabase."""
    return response


@router.post("/supabase/{path:path}")
async def supabase_post_proxy(response = Depends(proxy_with_body)):
    """Proxy function for forwarding a POST request with a body payload to Supabase."""
    return response


@router.patch("/supabase/{path:path}")
async def supabase_update_proxy(response = Depends(proxy_with_body)):
    """Proxy function for forwarding a PATCH request with a body payload to Supabase."""
    return response


@router.delete("/supabase/{path:path}")
async def supabase_delete_proxy(response = Depends(proxy)):
    """Proxy function for forwarding a DELETE request to Supabase."""
    return response

################################################



@router.post("/cloudinary-upload", dependencies=[Depends(get_current_user)])
async def cloudinary_upload(image: UploadFile = File(...)):
    """
    Upload an image to Cloudinary. This will return a JSON object containing
    urls for the image in common sizes, such as thumbnails and display sizes.
    """
    cloudinary_service = CloudinaryService(image.file)
    return await cloudinary_service.upload_to_cloudinary()

from typing import Generic
from beanie import PydanticObjectId
from fastapi_pagination import Params, Page
from pyflutterflow.database.firestore.firestore_client import FirestoreClient
from pyflutterflow.database.interface import BaseRepositoryInterface
from pyflutterflow.database import ModelType, CreateSchemaType, UpdateSchemaType
from app.services.auth import FirebaseUser
from app.logs import get_logger
from app import constants

logger = get_logger(__name__)


class FirestoreRepository(BaseRepositoryInterface[ModelType, CreateSchemaType, UpdateSchemaType], Generic[ModelType, CreateSchemaType, UpdateSchemaType]):

    def __init__(self, model: type[ModelType]):
        self.db = FirestoreClient.get_client()
        self.collection = self.db.collection(model.Settings.name)
        self.model = model

    async def list(self, params: Params, current_user: FirebaseUser) -> Page[ModelType]:
        raise NotImplementedError("Firestore paginated lists are not yet available in this Python API")

    async def list_all(self, params: Params, current_user: FirebaseUser) -> Page[ModelType]:
        raise NotImplementedError("Firestore paginated lists are not yet available in this Python API")

    async def get(self, id: str, current_user: FirebaseUser) -> ModelType:
        doc_ref = self.collection.document(id)
        doc = await doc_ref.get()
        if not doc.exists:
            raise ValueError
        data = doc.to_dict()
        if not data.get('user_id'):
            raise ValueError("Firestore document does not have a user_id field")
        if data.get('user_id') != current_user.uid and current_user.role != constants.ADMIN_ROLE:
            logger.warning(f"An attempt was made to retrieve a firestore record not owned by the current user. User: {current_user.uid}, Record: {doc.id}")
            raise ValueError("Attempted to access a record without privileges.")
        return self.model(**data)

    async def create(self, data: CreateSchemaType, current_user: FirebaseUser, **kwargs) -> ModelType:
        data = data.to_dict()
        data['user_id'] = current_user.uid
        data['id'] = kwargs.get('id', str(PydanticObjectId()))
        return await self.model(**data).fs_create()

    async def update(self, id: str, data: UpdateSchemaType, current_user: FirebaseUser) -> ModelType:
        doc_ref = self.collection.document(id)
        await doc_ref.update(data.to_dict())
        return data

    async def delete(self, id: str, current_user: FirebaseUser) -> None:
        document = await self.get(id, current_user)
        await document.fs_delete()

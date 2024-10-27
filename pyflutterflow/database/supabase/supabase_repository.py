from typing import Generic
from cachetools import TTLCache
from fastapi_pagination import Params, Page
from pyflutterflow.database.supabase.supabase_client import SupabaseClient
from pyflutterflow.database.interface import BaseRepositoryInterface
from pyflutterflow.database import ModelType, CreateSchemaType, UpdateSchemaType
from pyflutterflow.auth import FirebaseUser
from pyflutterflow.logs import get_logger

logger = get_logger(__name__)
token_cache = TTLCache(maxsize=100, ttl=300)


class SupabaseRepository(
    BaseRepositoryInterface[ModelType, CreateSchemaType, UpdateSchemaType],
    Generic[ModelType, CreateSchemaType, UpdateSchemaType],
):
    """
    A repository class for interacting with Supabase, providing CRUD operations for a given model.
    """

    def __init__(self, model: type[ModelType]):
        """
        Initializes the repository with the given model.

        Args:
            model (type[ModelType]): The model class to use for database operations.

        Raises:
            ValueError: If the model does not have a Settings class with a 'name' attribute.
        """
        self.model = model
        if not hasattr(model, "Settings") or not getattr(model.Settings, "name", None):
            raise ValueError(
                "Model does not have a Settings class. Tables must be named within a Settings class in the model."
            )
        self.table_name = model.Settings.name
        self.supabase = SupabaseClient()

    def paginator(self, params: Params):
        """
        Calculates the start and end indices for pagination based on the given parameters.

        Args:
            params (Params): The pagination parameters.

        Returns:
            Tuple[int, int]: The start and end indices for the pagination.
        """
        start = (params.page - 1) * params.size
        end = start + params.size - 1
        return start, end

    def get_token(self, user_id: str) -> dict:
        """
        Generates a JWT token for the user, caching it to reduce unnecessary token generation.

        Args:
            user_id (str): The user ID for whom to generate the token.

        Returns:
            dict: A dictionary containing the 'Authorization' header with the Bearer token.
        """
        if user_id in token_cache:
            jwt_token = token_cache[user_id]
        else:
            jwt_token = self.supabase.generate_jwt(user_id)
            token_cache[user_id] = jwt_token

        return {
            'Authorization': f'Bearer {jwt_token}',
        }

    async def list(self, params: Params, current_user: FirebaseUser) -> Page[ModelType]:
        """
        Retrieves a paginated list of records associated with the current user.

        Args:
            params (Params): The pagination parameters.
            current_user (FirebaseUser): The currently authenticated user.

        Returns:
            Page[ModelType]: A paginated list of records belonging to the current user.
        """
        client = await self.supabase.get_client()
        pager = self.paginator(params)

        response = (
            await client.table(self.table_name)
            .select("*")
            .eq("user_id", current_user.uid)
            .range(*pager)
            .execute()
        )
        total_response = (
            await client.table(self.table_name)
            .select("id", count="exact")
            .eq("user_id", current_user.uid)
            .execute()
        )
        total = total_response.count or 0
        items = [self.model(**item) for item in response.data]
        return Page.create(items=items, total=total, params=params)

    async def build_paginated_query(self, params: Params, current_user: FirebaseUser, sql_query: str, auth: bool) -> Page[ModelType]:
        """
        Builds a paginated query for fetching records, optionally with authentication headers.

        Args:
            params (Params): The pagination parameters.
            current_user (FirebaseUser): The currently authenticated user.
            sql_query (str): The SQL query string for selecting fields.
            auth (bool): Whether to include authentication headers in the query.

        Returns:
            Any: The constructed query object ready for execution.
        """
        client = await self.supabase.get_client()

        # Build the query
        pager = self.paginator(params)
        query = (
            client.table(self.table_name)
            .select(sql_query, count="exact")
            .range(*pager)
        )

        # Set the auth header
        if auth:
            headers = self.get_token(current_user.uid)
            query.headers.update(headers)

        return query

    async def build_query(self, current_user: FirebaseUser, sql_query: str = '*', auth: bool = True, table=None) -> Page[ModelType]:
        """
        Builds a query for fetching records from the specified table, optionally with authentication headers.

        Args:
            current_user (FirebaseUser): The currently authenticated user.
            sql_query (str, optional): The SQL query string for selecting fields. Defaults to '*'.
            auth (bool, optional): Whether to include authentication headers in the query. Defaults to True.
            table (str, optional): The name of the table to query. Defaults to None, which uses the model's table name.

        Returns:
            Any: The constructed query object ready for execution.
        """
        client = await self.supabase.get_client()
        if not table:
            table = self.table_name
        query = client.table(table).select(sql_query)

        if auth:
            headers = self.get_token(current_user.uid)
            query.headers.update(headers)

        return query

    async def list_all(self, params: Params, current_user: FirebaseUser, **kwargs) -> Page[ModelType]:
        """
        Retrieves a paginated and optionally sorted list of all records.

        Args:
            params (Params): The pagination parameters.
            current_user (FirebaseUser): The currently authenticated user.
            **kwargs: Additional keyword arguments.
                - sql_query (str, optional): The SQL query string for selecting fields. Defaults to '*'.
                - auth (bool, optional): Whether to include authentication headers in the query. Defaults to True.
                - sort_by (str, optional): The field name to sort the records by.

        Returns:
            Page[ModelType]: A paginated list of records.
        """
        sql_query = kwargs.get('sql_query', '*')
        auth = kwargs.get('auth', True)
        query = await self.build_paginated_query(params, current_user, sql_query, auth)

        if kwargs.get("sort_by"):
            query = query.order(kwargs.get("sort_by"))

        response = await query.execute()

        items = [self.model(**item) for item in response.data]
        return Page.create(items=items, total=response.count, params=params)

    async def get(self, pk: int, current_user: FirebaseUser, auth=True) -> ModelType:
        """
        Retrieves a single record by primary key (ID), ensuring it belongs to the current user.

        Args:
            pk (int): The primary key (ID) of the record to retrieve.
            current_user (FirebaseUser): The currently authenticated user.
            auth (bool, optional): Whether to include authentication headers in the query. Defaults to True.

        Returns:
            ModelType: The retrieved record.

        Raises:
            ValueError: If the record does not exist or the operation fails.
        """
        # Create the query
        client = await self.supabase.get_client()
        query = client.table(self.table_name).select("*").eq("id", pk)

        # Set the auth header
        if auth:
            headers = self.get_token(current_user.uid)
            query.headers.update(headers)

        try:
            response = await query.execute()
        except Exception as e:
            logger.error(f"Error creating record: {e}")
            raise ValueError(f"Get operation failed: {e}")
        return self.model(**response.data[0])

    async def create(self, data: CreateSchemaType, current_user: FirebaseUser, **kwargs) -> ModelType:
        """
        Creates a new record with the current user's ID.

        Args:
            data (CreateSchemaType): The data to create the record.
            current_user (FirebaseUser): The currently authenticated user.

        Returns:
            ModelType: The created record.

        Raises:
            ValueError: If the create operation fails.
        """
        client = await self.supabase.get_client()
        serialized_data = data.model_dump(mode='json')
        query = client.table(self.table_name).insert(serialized_data)

        try:
            response = await query.execute()
        except Exception as e:
            logger.error(f"Error creating record: {e}")
            raise ValueError(f"Insert operation failed: {e}")
        return self.model(**response.data[0])

    async def update(self, pk: int, data: UpdateSchemaType, current_user: FirebaseUser) -> ModelType:
        """
        Updates an existing record by primary key (ID), ensuring it belongs to the current user.

        Args:
            pk (int): The primary key (ID) of the record to update.
            data (UpdateSchemaType): The data to update the record with.
            current_user (FirebaseUser): The currently authenticated user.

        Raises:
            ValueError: If the update operation fails.
        """
        client = await self.supabase.get_client()
        serialized_data = data.model_dump(mode='json', exclude_none=True)
        query = client.table(self.table_name).update(serialized_data).eq("id", pk)
        await query.execute()

    async def delete(self, pk: int, current_user: FirebaseUser) -> None:
        """
        Deletes a record by primary key (ID). Note this is an admin
        operation and does not check if the record belongs to the current user.
        Use
            await query.delete().eq("id", pk).eq("user_id", current_user.uid).execute()
        explicitly to check for ownership.

        Args:
            pk (int): The primary key (ID) of the record to delete.
            current_user (FirebaseUser): The currently authenticated user.

        Raises:
            ValueError: If the delete operation fails.
        """
        client = await self.supabase.get_client()
        query = client.table(self.table_name)

        try:
            await query.delete().eq("id", pk).execute()
        except Exception as e:
            logger.error("Error deleting record: %s", e)
            raise ValueError(f"Delete operation failed: {e}") from e
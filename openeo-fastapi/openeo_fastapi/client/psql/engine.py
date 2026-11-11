# Modified by the JonaAI Authors from upstream openeo-fastapi (Apache-2.0); see /NOTICE.
"""Standardisation of common functionality to interact with the ORMs and the database.
"""
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from typing import Any, Union                                                                                                                                                    

from openeo_fastapi.client.psql.settings import DataBaseSettings

# Module-level engine singleton to prevent creating a new engine per DB operation.
# Previously, every call to get/create/modify/delete created a new engine+pool,
# leading to connection exhaustion under load.
_engine = None


def get_engine():
    """Get or create the singleton engine using config from pydantic settings.

    Returns:
        Engine: The shared engine instance.
    """
    global _engine
    if _engine is None:
        db_settings = DataBaseSettings()
        _engine = create_engine(
            url="postgresql://{}:{}@{}:{}/{}".format(
                db_settings.POSTGRES_USER.get_secret_value(),
                db_settings.POSTGRES_PASSWORD.get_secret_value(),
                db_settings.POSTGRESQL_HOST.get_secret_value(),
                db_settings.POSTGRESQL_PORT.get_secret_value(),
                db_settings.POSTGRES_DB.get_secret_value(),
            ),
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
            pool_pre_ping=True,
            # Bound dead-socket hangs. Without these, a half-open TCP connection
            # (peer gone, no RST) makes any query — including the pool_pre_ping
            # SELECT 1 — block forever, freezing the async event loop that runs
            # the Earth-Intelligence worker's synchronous DB calls.
            connect_args={
                "connect_timeout": 10,
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            },
        )
    return _engine


class Filter(BaseModel):
    """Filter class to assist with providing a filter by funciton with values across different cases."""

    column_name: str
    value: Any


def create(create_object: BaseModel) -> bool:
    """Add the values from a pydantic model to the database using its respective object relational mapping."""
    db = sessionmaker(get_engine())

    orm = create_object.get_orm()(**create_object.model_dump())

    with db.begin() as session:
        session.add(orm)
    return True


def get(get_model: BaseModel, primary_key: Any) -> Union[None, BaseModel]:
    """Get the relevant entry for a given model using the provided primary key value.

    Args:
        get_model (BaseModel): The model that to get from the database.
        primary_key (Any): The primary key of the model instance to get.

    Returns:
        Union[None, BaseModel]: None, or the found model
    """
    db = sessionmaker(get_engine())

    with db.begin() as session:
        if isinstance(primary_key, list):
            found = session.get(get_model.get_orm(), primary_key)
        else:
            found = session.get(get_model.get_orm(), str(primary_key))

        if not found:
            return None
        obj = get_model.model_validate(found)
    return obj


def _list(list_model: BaseModel, filter_with: Filter) -> list[BaseModel]:
    """List all relevant entries for a given model for a given filter.

    Args:
        get_model (BaseModel): The model that to list from the database.
        filter_with (Filter): Filter of a Key/Value pair to apply to the model.

    Returns:
        list[BaseModel]: A list of models found matching the filter.
    """
    db = sessionmaker(get_engine())

    with db.begin() as session:
        # Sessions API has no list function, so prepare the statement with select and apply to scalar.
        if filter_with == None:
            query_statement = select(list_model.get_orm())
        else:
            query_statement = select(list_model.get_orm()).filter_by(
                **{filter_with.column_name: filter_with.value}
            )
        objs = session.scalars(query_statement)
        found = [list_model.model_validate(obj) for obj in objs]
    return found


def modify(modify_object: BaseModel) -> bool:
    """Modify the relevant entries for a given model instance

    Args:
        modify_object (BaseModel): An instance of a pydantic model that reflects a change to make in the database.

    Returns:
        bool: Whether the change was successful.
    """
    db = sessionmaker(get_engine())

    with db.begin() as session:
        session.merge(modify_object.get_orm()(**modify_object.model_dump()))
    return True


def delete(delete_model: BaseModel, primary_key: Any) -> bool:
    """Delete the values from a pydantic model in the database using its respective object relational mapping.

    Args:
        delete_model (BaseModel): The model that to delete from the database.
        primary_key (Any): The primary key of the model instance to delete.

    Returns:
        bool: Whether the change was successful.
    """
    db = sessionmaker(get_engine())

    with db.begin() as session:
        if isinstance(primary_key, list):
            delete_obj = session.get(delete_model.get_orm(), primary_key)
        else:
            delete_obj = session.get(delete_model.get_orm(), str(primary_key))

        session.delete(delete_obj)
    return True


def get_first_or_default(get_model: BaseModel, filter_with: Filter) -> BaseModel:
    """Perform a list operation and return the first found instance.

    Args:
        get_model (BaseModel): The model that to get from the database.
        filter_with (Filter): Filter of a Key/Value pair to apply to the model.

    Returns:
        Union[None, BaseModel]: Return the model if found, else return None.
    """
    user_exists = _list(filter_with=filter_with, list_model=get_model)
    if user_exists:
        return user_exists[0]
    return None

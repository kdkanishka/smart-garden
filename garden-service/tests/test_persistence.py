import asyncio
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from pydantic import ValidationError
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

import crud
from database import SCHEMA_REVISION, Settings, check_schema, create_database, create_session_factory
from models import Plant
from schemas import PlantCreate, PlantPatch


URL = "postgresql+psycopg://user:password@localhost/garden_test"


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def test_create_commits_and_uses_database_generated_values():
    plant = object()
    session = AsyncMock()
    session.execute.return_value = Mock(scalar_one=Mock(return_value=plant))
    result = asyncio.run(crud.create_plant(session, PlantCreate(name=" Mint ", lat=1, lon=2), "demo-user-1"))
    assert result is plant
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["name"] == "Mint"
    assert compiled.params["owner_id"] == "demo-user-1"
    assert "created_at" not in compiled.params
    assert "id" not in compiled.params
    assert "RETURNING plants.id" in str(compiled)
    session.commit.assert_awaited_once()


def test_patch_updates_only_supplied_fields_and_timestamp():
    session = AsyncMock()
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=object()))
    asyncio.run(crud.update_plant(session, 7, PlantPatch(name=" Rose "), "demo-user-1"))
    statement = session.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert compiled.params["name"] == "Rose"
    assert "lat" not in compiled.params and "lon" not in compiled.params
    assert "created_at" not in compiled.params
    assert "updated_at=clock_timestamp()" in str(compiled)
    assert "WHERE plants.id =" in str(compiled)
    assert 7 in compiled.params.values()
    assert "plants.owner_id =" in str(compiled)
    assert "demo-user-1" in compiled.params.values()
    session.commit.assert_awaited_once()


def test_list_and_get_filter_by_owner():
    session = AsyncMock()
    session.scalars.return_value = Mock(all=Mock(return_value=["first", "second"]))
    assert asyncio.run(crud.list_plants(session, "demo-user-1")) == ["first", "second"]
    assert "ORDER BY plants.id" in sql(session.scalars.await_args.args[0])
    statement = session.scalars.await_args.args[0]
    assert "plants.owner_id =" in sql(statement)
    assert "demo-user-1" in statement.compile().params.values()
    asyncio.run(crud.get_plant(session, 7, "demo-user-1"))
    statement = session.scalar.await_args.args[0]
    assert "plants.id =" in sql(statement) and "plants.owner_id =" in sql(statement)
    assert "demo-user-1" in statement.compile().params.values()


@pytest.mark.parametrize("deleted_id,expected", [(1, True), (None, False)])
def test_delete_commits_and_reports_missing_rows(deleted_id, expected):
    session = AsyncMock()
    session.execute.return_value = Mock(scalar_one_or_none=Mock(return_value=deleted_id))
    assert asyncio.run(crud.delete_plant(session, 1, "demo-user-1")) is expected
    assert "RETURNING plants.id" in sql(session.execute.await_args.args[0])
    statement = session.execute.await_args.args[0]
    assert "plants.owner_id =" in sql(statement)
    assert "demo-user-1" in statement.compile().params.values()
    session.commit.assert_awaited_once()


def test_failed_commit_is_not_reported_as_success():
    session = AsyncMock()
    session.execute.return_value = Mock(scalar_one=Mock(return_value=object()))
    session.commit.side_effect = OperationalError("COMMIT", {}, Exception())
    with pytest.raises(OperationalError):
        asyncio.run(crud.create_plant(session, PlantCreate(name="Mint", lat=0, lon=0), "demo-user-1"))


@pytest.mark.parametrize("revision", [[], ["old_revision"], [SCHEMA_REVISION, "other"]])
def test_readiness_rejects_wrong_migration_revision(revision):
    session = AsyncMock()
    session.execute.return_value = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=revision))))
    with pytest.raises(HTTPException) as error:
        asyncio.run(check_schema(session))
    assert error.value.status_code == 503


def test_readiness_queries_expected_columns():
    session = AsyncMock()
    session.execute.return_value = Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[SCHEMA_REVISION]))))
    asyncio.run(check_schema(session))
    statement = sql(session.execute.await_args.args[0])
    for name in ["id", "owner_id", "name", "lat", "lon", "created_at", "updated_at"]:
        assert f"plants.{name}" in statement


@pytest.mark.parametrize("url", ["", "not a URL", "sqlite:///test.db", "postgresql://localhost/db", "postgresql+psycopg://localhost"])
def test_invalid_database_configuration(url):
    with pytest.raises(ValidationError):
        Settings(database_url=url)


def test_async_psycopg_engine_and_session_configuration():
    engine = create_database(Settings(database_url=URL))
    try:
        assert engine.dialect.is_async
        assert engine.dialect.driver == "psycopg"
        assert create_session_factory(engine).kw["expire_on_commit"] is False
    finally:
        asyncio.run(engine.dispose())


def test_initial_migration_generates_postgresql_ddl_and_downgrade(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    output = StringIO()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    ddl = output.getvalue()
    for expected in [
        "CREATE TABLE plants", "id SERIAL NOT NULL", "name VARCHAR(100) NOT NULL",
        "owner_id VARCHAR(100) NOT NULL", "CREATE INDEX ix_plants_owner_id ON plants (owner_id)",
        "lat NUMERIC(8, 6) NOT NULL", "lon NUMERIC(9, 6) NOT NULL",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "PRIMARY KEY (id)", "lat BETWEEN -90 AND 90", "lon BETWEEN -180 AND 180",
        "char_length(name) BETWEEN 1 AND 100", "name = btrim(name)", SCHEMA_REVISION,
    ]:
        assert expected in ddl
    assert "UNIQUE" not in ddl
    output.seek(0)
    output.truncate()
    command.downgrade(config, f"{SCHEMA_REVISION}:base", sql=True)
    assert "DROP TABLE plants" in output.getvalue()

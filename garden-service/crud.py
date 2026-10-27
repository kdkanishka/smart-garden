"""Async persistence operations; mutations commit before returning success."""

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Plant
from schemas import PlantCreate, PlantPatch


async def create_plant(session: AsyncSession, data: PlantCreate) -> Plant:
    plant = (await session.execute(
        insert(Plant).values(**data.model_dump()).returning(Plant)
    )).scalar_one()
    await session.commit()
    return plant


async def list_plants(session: AsyncSession) -> list[Plant]:
    return list((await session.scalars(select(Plant).order_by(Plant.id))).all())


async def get_plant(session: AsyncSession, plant_id: int) -> Plant | None:
    return await session.get(Plant, plant_id)


async def update_plant(session: AsyncSession, plant_id: int, data: PlantPatch) -> Plant | None:
    plant = (await session.execute(
        update(Plant).where(Plant.id == plant_id)
        .values(**data.model_dump(exclude_unset=True)).returning(Plant)
    )).scalar_one_or_none()
    await session.commit()
    return plant


async def delete_plant(session: AsyncSession, plant_id: int) -> bool:
    deleted_id = (await session.execute(
        delete(Plant).where(Plant.id == plant_id).returning(Plant.id)
    )).scalar_one_or_none()
    await session.commit()
    return deleted_id is not None

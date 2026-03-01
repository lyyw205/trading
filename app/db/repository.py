from typing import Any, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """공통 CRUD 베이스 리포지토리."""

    def __init__(self, model: type[ModelT], session: AsyncSession):
        self._model = model
        self._session = session

    async def get_by_id(self, id_val: Any) -> ModelT | None:
        return await self._session.get(self._model, id_val)

    async def create(self, obj: ModelT) -> ModelT:
        self._session.add(obj)
        await self._session.flush()
        return obj

    async def delete_by_id(self, id_val: Any) -> None:
        obj = await self.get_by_id(id_val)
        if obj:
            await self._session.delete(obj)
            await self._session.flush()

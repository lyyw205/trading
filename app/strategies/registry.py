from __future__ import annotations
from app.strategies.base import BaseBuyLogic, BaseSellLogic


class BuyLogicRegistry:
    """매수 로직 플러그인 등록/조회."""
    _buy_logics: dict[str, type[BaseBuyLogic]] = {}

    @classmethod
    def register(cls, buy_logic_class: type[BaseBuyLogic]):
        cls._buy_logics[buy_logic_class.name] = buy_logic_class
        return buy_logic_class

    @classmethod
    def get(cls, name: str) -> type[BaseBuyLogic]:
        if name not in cls._buy_logics:
            raise KeyError(f"Unknown buy logic: {name}")
        return cls._buy_logics[name]

    @classmethod
    def create_instance(cls, name: str) -> BaseBuyLogic:
        return cls.get(name)()

    @classmethod
    def list_all(cls) -> list[dict]:
        return [
            {"name": c.name, "display_name": c.display_name,
             "description": c.description, "version": c.version,
             "default_params": c.default_params, "tunable_params": c.tunable_params}
            for c in (cls._buy_logics[k]() for k in cls._buy_logics)
        ]


class SellLogicRegistry:
    """매도 로직 플러그인 등록/조회."""
    _sell_logics: dict[str, type[BaseSellLogic]] = {}

    @classmethod
    def register(cls, sell_logic_class: type[BaseSellLogic]):
        cls._sell_logics[sell_logic_class.name] = sell_logic_class
        return sell_logic_class

    @classmethod
    def get(cls, name: str) -> type[BaseSellLogic]:
        if name not in cls._sell_logics:
            raise KeyError(f"Unknown sell logic: {name}")
        return cls._sell_logics[name]

    @classmethod
    def create_instance(cls, name: str) -> BaseSellLogic:
        return cls.get(name)()

    @classmethod
    def list_all(cls) -> list[dict]:
        return [
            {"name": c.name, "display_name": c.display_name,
             "description": c.description, "version": c.version,
             "default_params": c.default_params, "tunable_params": c.tunable_params}
            for c in (cls._sell_logics[k]() for k in cls._sell_logics)
        ]

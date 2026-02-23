from __future__ import annotations
from app.strategies.base import BaseStrategy


class StrategyRegistry:
    """전략 플러그인 등록/조회. 인스턴스는 AccountTrader에서 캐시."""

    _strategies: dict[str, type[BaseStrategy]] = {}

    @classmethod
    def register(cls, strategy_class: type[BaseStrategy]):
        cls._strategies[strategy_class.name] = strategy_class
        return strategy_class

    @classmethod
    def get(cls, name: str) -> type[BaseStrategy]:
        if name not in cls._strategies:
            raise KeyError(f"Unknown strategy: {name}")
        return cls._strategies[name]

    @classmethod
    def create_instance(cls, name: str) -> BaseStrategy:
        return cls.get(name)()

    @classmethod
    def list_all(cls) -> list[dict]:
        return [
            {"name": s.name, "display_name": s.display_name,
             "description": s.description, "version": s.version,
             "default_params": s.default_params, "tunable_params": s.tunable_params}
            for s in (cls._strategies[k]() for k in cls._strategies)
        ]

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar


class RegistryError(ValueError):
    pass


class DuplicateRegistrationError(RegistryError):
    pass


class UnknownComponentError(RegistryError):
    pass


T = TypeVar("T")
Factory = Callable[..., T]


@dataclass(slots=True)
class FactoryRegistry:
    component_kind: str
    _factories: dict[str, Factory[object]] = field(default_factory=dict)

    def register(self, component_id: str, factory: Factory[object]) -> None:
        normalized_id = _normalize_id(component_id)
        if normalized_id in self._factories:
            raise DuplicateRegistrationError(
                f"{self.component_kind} {normalized_id!r} is already registered"
            )
        self._factories[normalized_id] = factory

    def create(self, component_id: str, *args: object, **kwargs: object) -> object:
        normalized_id = _normalize_id(component_id)
        try:
            factory = self._factories[normalized_id]
        except KeyError as exc:
            available = ", ".join(self.available()) or "(none)"
            raise UnknownComponentError(
                f"Unknown {self.component_kind} {normalized_id!r}; available: {available}"
            ) from exc
        return factory(*args, **kwargs)

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


@dataclass(slots=True)
class ComponentRegistry:
    sources: FactoryRegistry = field(default_factory=lambda: FactoryRegistry("source"))
    channels: FactoryRegistry = field(default_factory=lambda: FactoryRegistry("channel"))
    steps: FactoryRegistry = field(default_factory=lambda: FactoryRegistry("step"))
    sinks: FactoryRegistry = field(default_factory=lambda: FactoryRegistry("sink"))

    def register_source(self, component_id: str, factory: Factory[object]) -> None:
        self.sources.register(component_id, factory)

    def register_channel(self, component_id: str, factory: Factory[object]) -> None:
        self.channels.register(component_id, factory)

    def register_step(self, component_id: str, factory: Factory[object]) -> None:
        self.steps.register(component_id, factory)

    def register_sink(self, component_id: str, factory: Factory[object]) -> None:
        self.sinks.register(component_id, factory)


def _normalize_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        raise RegistryError("component id must not be empty")
    return normalized

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from job_scraper.ports.channels import JobChannel
from job_scraper.ports.processors import PipelineStep
from job_scraper.ports.sinks import JobSink
from job_scraper.ports.sources import JobSource


class RegistryError(ValueError):
    pass


class DuplicateRegistrationError(RegistryError):
    pass


class UnknownComponentError(RegistryError):
    pass


T = TypeVar("T")
Factory = Callable[..., T]


@dataclass(slots=True)
class FactoryRegistry(Generic[T]):
    """Stable ids to factories for one kind of component.

    Parameterized by what it builds, so `create` returns that type instead of
    `object`. Without this every call site had to `cast` the result back, which
    is the same type information travelling by convention rather than by
    signature.
    """

    component_kind: str
    _factories: dict[str, Factory[T]] = field(default_factory=dict)

    def register(self, component_id: str, factory: Factory[T]) -> None:
        normalized_id = _normalize_id(component_id)
        if normalized_id in self._factories:
            raise DuplicateRegistrationError(
                f"{self.component_kind} {normalized_id!r} is already registered"
            )
        self._factories[normalized_id] = factory

    def create(self, component_id: str, *args: object, **kwargs: object) -> T:
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
    """The composition mechanism: one typed registry per extension point."""

    sources: FactoryRegistry[JobSource] = field(
        default_factory=lambda: FactoryRegistry[JobSource]("source")
    )
    channels: FactoryRegistry[JobChannel] = field(
        default_factory=lambda: FactoryRegistry[JobChannel]("channel")
    )
    steps: FactoryRegistry[PipelineStep] = field(
        default_factory=lambda: FactoryRegistry[PipelineStep]("step")
    )
    sinks: FactoryRegistry[JobSink] = field(
        default_factory=lambda: FactoryRegistry[JobSink]("sink")
    )

    def register_source(self, component_id: str, factory: Factory[JobSource]) -> None:
        self.sources.register(component_id, factory)

    def register_channel(self, component_id: str, factory: Factory[JobChannel]) -> None:
        self.channels.register(component_id, factory)

    def register_step(self, component_id: str, factory: Factory[PipelineStep]) -> None:
        self.steps.register(component_id, factory)

    def register_sink(self, component_id: str, factory: Factory[JobSink]) -> None:
        self.sinks.register(component_id, factory)


def _normalize_id(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        raise RegistryError("component id must not be empty")
    return normalized

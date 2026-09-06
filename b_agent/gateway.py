from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import Capability, Evidence, RetrievalBatch, RetrievalStep
from .retrievers import RetrievalContext, Retriever, RetrieverRegistry


@dataclass(frozen=True)
class CapabilitySnapshot:
    snapshot_id: str
    available: frozenset[Capability]
    registry_version: str


class DataGateway(Protocol):
    """Contract owned by B and implemented against A's storage/search layer."""

    def capability_snapshot(self) -> CapabilitySnapshot: ...

    def retrieve(
        self,
        capability: Capability,
        query: dict,
        upstream_evidence: dict[str, list[Evidence]],
        top_k: int,
    ) -> RetrievalBatch: ...


class GatewayRetriever(Retriever):
    def __init__(self, gateway: DataGateway, capability: Capability) -> None:
        self.gateway = gateway
        self.capability = capability

    def unsupported_reason(self, step: RetrievalStep) -> str | None:
        checker = getattr(self.gateway, "unsupported_reason", None)
        return checker(self.capability, step.query) if callable(checker) else None

    def retrieve(self, step: RetrievalStep, context: RetrievalContext) -> RetrievalBatch:
        upstream = {
            step_id: list(context.prior_results[step_id].evidence)
            for step_id in step.depends_on
            if step_id in context.prior_results
        }
        return self.gateway.retrieve(
            capability=self.capability,
            query=step.query,
            upstream_evidence=upstream,
            top_k=step.top_k,
        )


def registry_from_gateway(gateway: DataGateway) -> RetrieverRegistry:
    """Register only the capabilities declared by A's immutable snapshot."""
    snapshot = gateway.capability_snapshot()
    registry = RetrieverRegistry(snapshot_id=snapshot.snapshot_id)
    for capability in sorted(snapshot.available, key=lambda item: item.value):
        registry.register(capability, GatewayRetriever(gateway, capability))
    return registry

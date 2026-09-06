from __future__ import annotations

import hashlib

from .models import Capability, Evidence


CAPABILITY_PRIORITY = {
    "structured_search": 7,
    "holding_search": 6,
    "relation_search": 5,
    "document_search": 4,
    "event_search": 3,
    "keyword_search": 2,
    "vector_search": 1,
}


class EvidenceFusion:
    def fuse(
        self,
        evidence: list[Evidence],
        required_capabilities: list[Capability] | None = None,
        limit: int = 50,
    ) -> list[Evidence]:
        best: dict[str, Evidence] = {}
        for item in evidence:
            key = self._key(item)
            current = best.get(key)
            if current is None or self._rank(item) > self._rank(current):
                best[key] = item
        ranked = sorted(best.values(), key=self._rank, reverse=True)
        required = list(dict.fromkeys(required_capabilities or []))
        if not required:
            return ranked[:limit]

        quota = max(1, min(10, limit // max(2, len(required) * 2)))
        selected: list[Evidence] = []
        selected_ids: set[str] = set()
        for capability in required:
            group = [item for item in ranked if item.capability == capability]
            for item in group[:quota]:
                selected.append(item)
                selected_ids.add(item.evidence_id)
        for item in ranked:
            if len(selected) >= limit:
                break
            if item.evidence_id not in selected_ids:
                selected.append(item)
                selected_ids.add(item.evidence_id)
        return selected[:limit]

    @staticmethod
    def _key(item: Evidence) -> str:
        if item.evidence_id:
            return item.evidence_id
        payload = "|".join(
            [item.source_id, item.product_id or "", item.source_ref or "", item.content]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _rank(item: Evidence) -> tuple[float, float, str]:
        priority = float(CAPABILITY_PRIORITY.get(item.capability.value, 0))
        return priority, float(item.score), item.evidence_id

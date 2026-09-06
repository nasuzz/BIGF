from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_ENDPOINT_KEYS = {
    "source": ("source_entity_id", "source_entity_name"),
    "target": ("target_entity_id", "target_entity_name"),
}


def relation_result_values(
    relation: Mapping[str, Any], constraint: Mapping[str, Any]
) -> list[Any]:
    """Return the relation endpoint requested by a relation constraint.

    ``opposite`` is intentionally strict: when the anchor cannot be resolved to
    exactly one endpoint, returning no values is safer than searching holdings
    for an arbitrary company.
    """

    result_role = relation_result_role(relation, constraint)
    return _endpoint_values(relation, result_role) if result_role else []


def relation_result_role(
    relation: Mapping[str, Any], constraint: Mapping[str, Any]
) -> str | None:
    """Resolve a constraint's result endpoint to ``source`` or ``target``."""

    result_role = constraint.get("result_role")
    if result_role in _ENDPOINT_KEYS:
        return str(result_role)
    if result_role != "opposite":
        return None

    anchor = constraint.get("anchor")
    if not _nonempty(anchor):
        return None

    source_values = _endpoint_values(relation, "source")
    target_values = _endpoint_values(relation, "target")
    source_match = _anchor_match_level(anchor, source_values)
    target_match = _anchor_match_level(anchor, target_values)

    if source_match > target_match:
        return "target"
    if target_match > source_match:
        return "source"
    return None


def _endpoint_values(relation: Mapping[str, Any], role: str) -> list[Any]:
    return [
        relation.get(key)
        for key in _ENDPOINT_KEYS[role]
        if _nonempty(relation.get(key))
    ]


def _anchor_match_level(anchor: Any, endpoint_values: list[Any]) -> int:
    normalized_anchor = _normalize(anchor)
    if not normalized_anchor:
        return 0
    normalized_values = [_normalize(value) for value in endpoint_values]
    if normalized_anchor in normalized_values:
        return 2
    if any(
        normalized_anchor in value or value in normalized_anchor
        for value in normalized_values
        if value
    ):
        return 1
    return 0


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _nonempty(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))

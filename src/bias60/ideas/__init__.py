"""Idea implementations.

Each idea is one function ``provided_inputs -> IdeaOutput`` registered by its spec id. The registry
key must exist in ``config/ideas.json``; an id that is registered but absent from the registry, or
present in the registry but unimplemented, is a harness failure.
"""
from __future__ import annotations

from typing import Callable

from bias60.contracts import IdeaOutput

IMPLEMENTATIONS: dict[str, Callable[[object], IdeaOutput]] = {}


def implements(idea_id: str) -> Callable[[Callable[[object], IdeaOutput]], Callable[[object], IdeaOutput]]:
    def decorator(function: Callable[[object], IdeaOutput]) -> Callable[[object], IdeaOutput]:
        if idea_id in IMPLEMENTATIONS:
            raise ValueError(f"idea {idea_id} is registered twice")
        IMPLEMENTATIONS[idea_id] = function
        return function
    return decorator


def implemented_ids() -> list[str]:
    return sorted(IMPLEMENTATIONS)


from bias60.ideas import d as _d, dst as _dst, dst3 as _dst3, dst_meta as _dst_meta, g as _g, m as _m, mx as _mx, p as _p, p_physical as _p_physical, s as _s, sc as _sc, sc_schema as _sc_schema, sca as _sca, sca_extra as _sca_extra, tm as _tm, x as _x  # noqa: E402,F401  (registration import)

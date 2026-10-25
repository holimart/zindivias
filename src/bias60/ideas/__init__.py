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


from bias60.ideas import mx as _mx, p as _p, sc as _sc, tm as _tm  # noqa: E402,F401  (registration import)

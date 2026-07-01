"""Story Diff Engine — semantic comparison of story versions across editions.

Exposes the core components for headline comparison, body diffing,
trajectory computation, and the unified DiffEngine.
"""

from stories.headline_compare import HeadlineComparer
from stories.body_diff import BodyDiffer
from stories.trajectory import TrajectoryComputer
from stories.diff_engine import DiffEngine

__all__ = [
    "DiffEngine",
    "HeadlineComparer",
    "BodyDiffer",
    "TrajectoryComputer",
]

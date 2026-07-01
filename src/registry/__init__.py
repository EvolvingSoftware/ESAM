"""Edition Registry — edition metadata, comparison, and statistics."""

from .engine import EditionRegistry
from .comparer import EditionComparer
from .stats import EditionStats

__all__ = ["EditionRegistry", "EditionComparer", "EditionStats"]

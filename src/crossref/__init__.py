"""Cross-Reference Engine — multi-source topic detection, entity linking, signal boosting, topic clustering.

Detects when the same topic appears across multiple sources and boosts
signal strength accordingly. Groups items into topic clusters.
"""

from crossref.engine import CrossReferenceEngine
from crossref.linker import EntityLinker
from crossref.booster import SignalBooster
from crossref.clusterer import TopicClusterer

__all__ = [
    "CrossReferenceEngine",
    "EntityLinker",
    "SignalBooster",
    "TopicClusterer",
]

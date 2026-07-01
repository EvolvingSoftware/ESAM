"""Narrative Synthesizer — story narrative generation, arc detection, signal decay, and article ideas."""

from narrative.engine import NarrativeEngine
from narrative.arc_detector import ArcDetector
from narrative.decay import SignalDecayer
from narrative.ideas import ArticleIdeaGenerator

__all__ = [
    "NarrativeEngine",
    "ArcDetector",
    "SignalDecayer",
    "ArticleIdeaGenerator",
]

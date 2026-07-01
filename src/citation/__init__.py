"""Citation Map Generator — Sequential citation ID assignment and resolution.

Provides CitationEngine (ID generation + DB persistence), CitationResolver
(text rewriting + verification), and CitationMap (map building + LLM prompt formatting).
"""

from citation.engine import CitationEngine
from citation.resolver import CitationResolver
from citation.map import CitationMap
from citation.validator import CitationValidator, HallucinationDetector

__all__ = ["CitationEngine", "CitationResolver", "CitationMap", "CitationValidator", "HallucinationDetector"]

"""ArticleIdeaGenerator — generate article ideas from current signals and narratives."""

from __future__ import annotations

from typing import Any


class ArticleIdeaGenerator:
    """Generate article topic ideas by grouping related signals into coherent
    article concepts.

    Prioritizes:
    - Rising signals (growing stories worth deeper coverage)
    - Multi-source topics (stories appearing across multiple sources)
    - Controversial or high-signal angles
    """

    def generate(
        self,
        signals: list[dict],
        narratives: list[dict] | str | None = None,
        max_ideas: int = 5,
    ) -> list[dict]:
        """Generate article ideas from a set of signals.

        Args:
            signals: List of signal/story dicts. Each should have at least
                ``title``, and optionally ``signal_strength``,
                ``trajectory``, ``significance``.
            narratives: Optional list of narrative dicts or a combined
                narrative string for context.
            max_ideas: Maximum number of article ideas to return (default: 5).

        Returns:
            List of article idea dicts::

                [
                    {
                        "title": str,
                        "rationale": str,
                        "signals_involved": [str, ...],
                        "target_audience": str,
                    },
                    ...
                ]
        """
        if not signals:
            return []

        # Parse narratives for context
        narrative_text = ""
        if isinstance(narratives, str):
            narrative_text = narratives
        elif isinstance(narratives, list):
            # Extract narrative_text from dicts
            parts = []
            for n in narratives:
                if isinstance(n, dict):
                    parts.append(n.get("narrative_text", ""))
                elif isinstance(n, str):
                    parts.append(n)
            narrative_text = " ".join(parts)

        # Sort signals by priority
        def _priority(s: dict) -> float:
            # Rising signals get highest priority
            traj = str(s.get("trajectory", s.get("signal_trajectory", ""))).lower()
            traj_bonus = 2.0 if traj == "rising" else (1.0 if traj == "new" else 0.0)

            signal = float(s.get("signal_strength", s.get("significance", 0.5)))
            if isinstance(s.get("significance"), str):
                sig_map = {"high": 0.8, "medium": 0.5, "low": 0.2}
                signal = sig_map.get(s.get("significance", ""), 0.5)

            return signal + traj_bonus

        sorted_signals = sorted(signals, key=_priority, reverse=True)

        ideas: list[dict] = []

        # 1. Rising signals → "What's driving X?"
        rising = [s for s in sorted_signals if str(s.get("trajectory", "")).lower() == "rising"
                  or str(s.get("signal_trajectory", "")).lower() == "rising"]
        for signal in rising[:max_ideas]:
            title = signal.get("title", "Emerging Topic")
            ideas.append({
                "title": f"What's Driving the Rise of {title}?",
                "rationale": (
                    f"'{title}' is showing a rising trajectory, suggesting "
                    f"growing relevance and reader interest. An article "
                    f"exploring the drivers behind this trend would be timely."
                ),
                "signals_involved": [title],
                "target_audience": "Industry analysts and decision-makers",
            })

        # 2. Multi-source topics
        if signals:
            # Find signals that appear across multiple categories/sources
            # (using title similarity as a proxy)
            titles_seen: dict[str, list[str]] = {}
            for signal in signals:
                t = signal.get("title", "").strip().lower()
                if t and len(t) > 5:
                    if t not in titles_seen:
                        titles_seen[t] = []
                    source = signal.get("source", signal.get("source_name", "unknown"))
                    titles_seen[t].append(source)

            multi_source = [
                (title, sources) for title, sources in titles_seen.items()
                if len(set(sources)) >= 2
            ]
            for title, sources in multi_source[:max_ideas - len(ideas)]:
                if len(ideas) >= max_ideas:
                    break
                display_title = title[:80].title()
                ideas.append({
                    "title": f"Across the Web: {display_title}",
                    "rationale": (
                        f"'{title[:60]}' appears in {len(set(sources))} different "
                        f"source(s), indicating broad cross-platform relevance. "
                        f"An article synthesizing these perspectives would provide "
                        f"comprehensive coverage."
                    ),
                    "signals_involved": [title[:80]],
                    "target_audience": "General readers and cross-platform researchers",
                })

        # 3. High-signal / potentially controversial angles
        high_signal = [
            s for s in sorted_signals
            if float(s.get("signal_strength", 0)) >= 0.6
            and s not in rising[:max_ideas]
        ]
        for signal in high_signal[:max_ideas - len(ideas)]:
            if len(ideas) >= max_ideas:
                break
            title = signal.get("title", "Key Topic")
            ideas.append({
                "title": f"Deep Dive: {title}",
                "rationale": (
                    f"'{title}' has high signal strength "
                    f"({signal.get('signal_strength', 'N/A')}), indicating "
                    f"significant current interest. A comprehensive deep-dive "
                    f"article would capitalize on this momentum."
                ),
                "signals_involved": [title],
                "target_audience": "Specialists and subject matter experts",
            })

        return ideas[:max_ideas]

"""Connectors CLI — 'esam connectors list' and 'esam connectors fetch <name>'."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add src to path
SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def cmd_list() -> None:
    """List all available connectors."""
    from connectors import list_connectors
    from connectors.registry import ConnectorRegistry

    names = list_connectors()
    print(f"Available connectors ({len(names)}):")
    print("-" * 40)
    for name in names:
        cls = ConnectorRegistry.get(name)
        if cls:
            desc = cls.description
            auth = "🔒 auth" if cls.auth_required else "🔓 no auth"
            print(f"  {name:20s} {desc} ({auth})")
    print()


def cmd_fetch(name: str, config_json: str | None = None) -> None:
    """Fetch data from a connector."""
    from connectors.registry import ConnectorRegistry

    config = {}
    if config_json:
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON config: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        connector = ConnectorRegistry.create(name, config)
    except KeyError:
        print(f"Error: Unknown connector '{name}'", file=sys.stderr)
        print(f"Available: {ConnectorRegistry.list()}", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching from connector '{name}' with config: {json.dumps(config, indent=2)}")
    print("-" * 60)

    try:
        results = connector.fetch()
        print(f"Got {len(results)} results:")
        print(json.dumps(results, indent=2))
    except Exception as e:
        print(f"Error during fetch: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="ESAM Connectors CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # connectors list
    subparsers.add_parser("list", help="List available connectors")

    # connectors fetch <name>
    fetch_parser = subparsers.add_parser("fetch", help="Fetch data from a connector")
    fetch_parser.add_argument("name", help="Connector name")
    fetch_parser.add_argument("--config", "-c", help="JSON config string (e.g. '{\"subreddit\": \"python\"}')")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list()
    elif args.command == "fetch":
        cmd_fetch(args.name, args.config)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

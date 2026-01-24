#!/usr/bin/env python3
"""
CLI tool for importing OpenStreetMap data into the Digital Twin database.

Usage:
    python scripts/load_osm.py data/talavera.osm
    python scripts/load_osm.py data/talavera.osm --clear
    python scripts/load_osm.py data/talavera.osm --batch-size 500
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db.database import async_session_factory, init_db
from app.services.osm_loader import OSMLoader


def create_progress_callback() -> tuple[dict, callable]:
    """Create a progress callback that tracks state."""
    state = {"current_stage": "", "current_count": 0}

    def callback(stage: str, count: int) -> None:
        # Clear previous line if same stage
        if state["current_stage"] == stage:
            print(f"\r{stage}... {count}", end="", flush=True)
        else:
            if state["current_stage"]:
                print()  # New line for new stage
            print(f"{stage}... {count}", end="", flush=True)
            state["current_stage"] = stage
        state["current_count"] = count

    return state, callback


async def main() -> int:
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Import OpenStreetMap data into Digital Twin database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python scripts/load_osm.py data/talavera.osm
    python scripts/load_osm.py data/talavera.osm --clear
    python scripts/load_osm.py data/talavera.osm --batch-size 500

Supported formats:
    .osm    - OpenStreetMap XML format

To get OSM data for Talavera de la Reina:
    1. Go to https://overpass-turbo.eu/
    2. Use query for highways in Talavera bbox
    3. Export as .osm file

    Or download from Geofabrik:
    wget https://download.geofabrik.de/europe/spain/castilla-la-mancha-latest.osm.pbf
    osmium extract -b -4.90,39.88,-4.78,39.98 castilla-la-mancha-latest.osm.pbf -o talavera.osm
        """,
    )
    parser.add_argument(
        "filepath",
        help="Path to OSM file (.osm format)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of entities to insert per batch (default: 1000)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear existing nodes and edges before import",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress output",
    )

    args = parser.parse_args()

    # Validate file exists
    filepath = Path(args.filepath)
    if not filepath.exists():
        print(f"Error: File not found: {args.filepath}", file=sys.stderr)
        return 1

    # Setup progress callback
    progress_state, progress_callback = create_progress_callback()
    if args.quiet:
        progress_callback = None

    # Print header
    if not args.quiet:
        print(f"Loading OSM data from: {args.filepath}")
        if args.clear:
            print("Warning: Existing data will be cleared!")
        print()

    try:
        # Initialize database connection
        await init_db()

        # Create session and load data
        async with async_session_factory() as session:
            loader = OSMLoader(
                session,
                batch_size=args.batch_size,
                progress_callback=progress_callback,
            )

            stats = await loader.load_from_file(
                str(filepath),
                clear_existing=args.clear,
            )

        # Print final newline after progress
        if not args.quiet:
            print("\n")

        # Print results
        print("=" * 50)
        print("Import completed!")
        print("=" * 50)
        print(f"  Nodes parsed:    {stats.nodes_parsed:,}")
        print(f"  Nodes imported:  {stats.nodes_imported:,}")
        print(f"  Ways parsed:     {stats.ways_parsed:,}")
        print(f"  Edges imported:  {stats.edges_imported:,}")
        print(f"  Ways skipped:    {stats.ways_skipped:,}")
        print(f"  Errors:          {stats.errors}")
        print(f"  Duration:        {stats.duration_seconds:.2f}s")
        print("=" * 50)

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if not args.quiet:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

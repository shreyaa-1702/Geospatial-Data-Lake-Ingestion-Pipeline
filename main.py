#!/usr/bin/env python3
"""
Street View 360° Panorama Pipeline
════════════════════════════════════════════════════════════════════════════════
Enterprise-grade bulk downloader: turns a CSV of location names → geocoded
coordinates → Street View pano IDs → stitched 360° JPEG images + metadata CSV.

Usage
─────
  python main.py --locations locations.csv
  python main.py --locations locations.csv --zoom 4 --api-key YOUR_KEY
  python main.py --locations locations.csv --resume --dry-run

Run `python main.py --help` for full option reference.
════════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import Config
from pipeline import (
    Geocoder,
    MetadataExporter,
    PanoFinder,
    PanoramaStitcher,
    TileDownloader,
    load_locations,
)
from utils import sanitize_filename, setup_logging, unique_stem

logger = logging.getLogger(__name__)


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="streetview_pipeline",
        description=(
            "Download Google Street View 360° panoramas for a list of locations.\n"
            "Outputs stitched JPEG images + a metadata CSV."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  # Basic run (uses Nominatim geocoding + unofficial pano lookup):
  python main.py --locations locations.csv

  # With Google API key (more reliable geocoding + free metadata API):
  python main.py --locations locations.csv --api-key YOUR_KEY

  # High-res export at zoom 4:
  python main.py --locations locations.csv --zoom 4

  # Resume interrupted run — skip already-downloaded images:
  python main.py --locations locations.csv --resume

  # Dry-run: geocode + find panos, write CSV, no image downloads:
  python main.py --locations locations.csv --dry-run

  # Set custom output directory:
  python main.py --locations locations.csv --output /path/to/output
        """,
    )

    p.add_argument(
        "--locations", "-l",
        required=True,
        metavar="FILE",
        help="Path to locations CSV (columns: name, address) or .txt (one address per line)",
    )
    p.add_argument(
        "--config", "-c",
        default="config.yaml",
        metavar="FILE",
        help="YAML config file (default: config.yaml — created automatically if missing)",
    )
    p.add_argument(
        "--api-key", "-k",
        metavar="KEY",
        help="Google Maps API key (overrides config / env GOOGLE_API_KEY)",
    )
    p.add_argument(
        "--zoom", "-z",
        type=int,
        choices=range(6),
        metavar="0-5",
        help=(
            "Zoom level — higher = larger image & slower download.\n"
            "0=512px  1=1024px  2=2048px  3=4096px(default)  4=8192px  5=16384px"
        ),
    )
    p.add_argument(
        "--output", "-o",
        metavar="DIR",
        help="Root output directory (default: ./output)",
    )
    p.add_argument(
        "--concurrent", "-j",
        type=int,
        metavar="N",
        help="Parallel tile downloads per panorama (default: 8)",
    )
    p.add_argument(
        "--radius",
        type=int,
        metavar="METRES",
        help="Pano search radius in metres (default: 100)",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip locations whose output image already exists",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Geocode + find panos only; write CSV without downloading images",
    )
    p.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console / file log verbosity (default: INFO)",
    )
    return p


# ── Pipeline ───────────────────────────────────────────────────────────────────

def run_pipeline(config: Config, locations_file: str) -> int:
    """
    Execute the full pipeline.

    Returns:
        Exit code (0 = success, 1 = partial failures, 2 = total failure).
    """
    # ── Setup output dirs ──────────────────────────────────────────────────
    config.images_dir.mkdir(parents=True, exist_ok=True)
    Path(config.output_dir).mkdir(parents=True, exist_ok=True)

    # ── Load locations ─────────────────────────────────────────────────────
    try:
        locations = load_locations(locations_file)
    except (FileNotFoundError, ValueError) as exc:
        logger.error(f"Failed to load locations: {exc}")
        return 2

    total = len(locations)
    logger.info("\n" + config.summary())
    logger.info(f"\nLocations loaded : {total}")
    logger.info(f"Images directory : {config.images_dir}")
    logger.info(f"Metadata CSV     : {config.csv_path}\n")
    logger.info("─" * 70)

    # ── Initialise components ──────────────────────────────────────────────
    geocoder = Geocoder(api_key=config.google_api_key, max_retries=config.max_retries)
    pano_finder = PanoFinder(
        api_key=config.google_api_key,
        radius=config.search_radius,
        timeout=config.request_timeout,
        max_retries=config.max_retries,
    )
    downloader = TileDownloader(config)
    stitcher = PanoramaStitcher(config)
    exporter = MetadataExporter(config)

    cols, rows = config.zoom_grid
    results: List[Dict[str, Any]] = []
    success_count = 0
    start_time = time.monotonic()

    # ── Process each location ──────────────────────────────────────────────
    for idx, loc in enumerate(locations, start=1):
        location_start = time.monotonic()
        processed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"\n[{idx}/{total}] {loc.name!r}")
        logger.info(f"  Address : {loc.address}")

        # ── Base record ────────────────────────────────────────────────────
        record: Dict[str, Any] = {
            "name": loc.name,
            "address": loc.address,
            "processed_at": processed_at,
        }

        # ── Resolve image path ─────────────────────────────────────────────
        stem = sanitize_filename(loc.name)
        if config.resume:
            existing = config.images_dir / f"{stem}.jpg"
            if existing.exists():
                logger.info(f"  ↷ Skipping — image already exists: {existing.name}")
                record.update(
                    status="SKIPPED",
                    image_filename=existing.name,
                    image_path=str(existing),
                )
                results.append(record)
                continue

        # ── Step 1: Geocode ────────────────────────────────────────────────
        # Skip if a pano_id was pre-supplied in the CSV
        pano_id_override: Optional[str] = loc.pano_id
        lat = lng = None
        street_name = formatted_address = ""

        if pano_id_override:
            logger.info(f"  ✓ Pre-supplied pano ID: {pano_id_override}")
            # We still need some coordinates; use 0,0 as placeholder
            lat, lng = 0.0, 0.0
        else:
            geo = geocoder.geocode(loc.address)
            if not geo:
                logger.error(f"  ✗ Geocoding failed")
                record.update(status="GEOCODE_FAILED", error="Geocoding returned no result")
                results.append(record)
                continue

            lat = geo.lat
            lng = geo.lng
            formatted_address = geo.formatted_address
            street_name = geo.street_name

            logger.info(f"  ✓ Geocoded   : {lat:.6f}, {lng:.6f}")
            if street_name:
                logger.info(f"  ✓ Street     : {street_name}")

        # ── Step 2: Find panorama ──────────────────────────────────────────
        if pano_id_override:
            from pipeline.pano_finder import PanoMetadata
            pano = PanoMetadata(
                pano_id=pano_id_override,
                lat=lat,
                lng=lng,
            )
        else:
            pano = pano_finder.find(lat, lng)
            if not pano:
                logger.error(f"  ✗ No Street View panorama found within {config.search_radius} m")
                record.update(
                    formatted_address=formatted_address,
                    street_name=street_name,
                    latitude=lat,
                    longitude=lng,
                    status="NO_STREETVIEW",
                    error=f"No panorama found within {config.search_radius} m",
                )
                results.append(record)
                continue

        logger.info(f"  ✓ Pano ID    : {pano.pano_id}")
        if pano.lat and pano.lng:
            logger.info(f"  ✓ Pano loc   : {pano.lat:.6f}, {pano.lng:.6f}")
        if pano.date:
            logger.info(f"  ✓ Captured   : {pano.date}")

        record.update(
            formatted_address=formatted_address,
            street_name=street_name or "",
            latitude=pano.lat,
            longitude=pano.lng,
            pano_id=pano.pano_id,
            capture_date=pano.date,
        )

        # ── Dry-run bail-out ───────────────────────────────────────────────
        if config.dry_run:
            record["status"] = "DRY_RUN"
            results.append(record)
            continue

        # ── Step 3: Download tiles ─────────────────────────────────────────
        tiles = downloader.download_all_tiles(pano.pano_id, cols, rows)
        if tiles is None:
            logger.error(f"  ✗ Tile download failed")
            record.update(
                status="DOWNLOAD_FAILED",
                error="Too many tile failures",
            )
            results.append(record)
            continue

        # ── Step 4: Stitch panorama ────────────────────────────────────────
        panorama = stitcher.stitch(tiles, cols, rows)

        # ── Step 5: Save image ─────────────────────────────────────────────
        image_path = unique_stem(stem, config.images_dir, ".jpg")
        panorama.save(str(image_path), "JPEG", quality=config.image_quality)
        file_size_mb = round(image_path.stat().st_size / 1_048_576, 2)
        elapsed = time.monotonic() - location_start

        logger.info(
            f"  ✓ Saved      : {image_path.name}  "
            f"({panorama.width}×{panorama.height} px, "
            f"{file_size_mb} MB, {elapsed:.1f}s)"
        )

        record.update(
            image_filename=image_path.name,
            image_path=str(image_path.resolve()),
            image_width=panorama.width,
            image_height=panorama.height,
            file_size_mb=file_size_mb,
            zoom_level=config.zoom_level,
            status="SUCCESS",
        )
        results.append(record)
        success_count += 1

        # Courtesy delay between locations
        if idx < total:
            time.sleep(config.rate_limit_delay)

    # ── Export metadata ────────────────────────────────────────────────────
    exporter.save(results)

    # ── Summary ────────────────────────────────────────────────────────────
    total_elapsed = time.monotonic() - start_time
    failures = total - success_count - sum(
        1 for r in results if r.get("status") == "SKIPPED"
    )

    logger.info("\n" + "═" * 70)
    logger.info("Pipeline complete")
    logger.info(f"  Total locations : {total}")
    logger.info(f"  ✓ Success       : {success_count}")
    logger.info(f"  ✗ Failed        : {failures}")
    logger.info(f"  ↷ Skipped       : {sum(1 for r in results if r.get('status') == 'SKIPPED')}")
    logger.info(f"  Wall time       : {total_elapsed:.1f}s")
    logger.info(f"  Metadata CSV    : {config.csv_path}")
    if failures:
        logger.info(f"  Failed CSV      : {config.failed_csv_path}")
    logger.info("═" * 70)

    if success_count == 0 and total > 0:
        return 2
    if failures > 0:
        return 1
    return 0


# ── Entrypoint ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # ── Load config ────────────────────────────────────────────────────────
    config_path = Path(args.config)
    if config_path.exists():
        config = Config.from_yaml(config_path)
    else:
        config = Config.from_env()

    # ── CLI overrides ──────────────────────────────────────────────────────
    if args.api_key:
        config.google_api_key = args.api_key
    if args.zoom is not None:
        config.zoom_level = args.zoom
    if args.output:
        config.output_dir = args.output
    if args.concurrent:
        config.concurrent_tiles = args.concurrent
    if args.radius:
        config.search_radius = args.radius
    if args.resume:
        config.resume = True
    if args.dry_run:
        config.dry_run = True
    if args.log_level:
        config.log_level = args.log_level

    # ── Logging ────────────────────────────────────────────────────────────
    log_file = (
        str(Path(config.output_dir) / config.log_file)
        if config.log_file
        else None
    )
    setup_logging(config.log_level, log_file)

    logger.info("Street View 360° Panorama Pipeline  v1.0")
    logger.info("─" * 70)

    sys.exit(run_pipeline(config, args.locations))


if __name__ == "__main__":
    main()

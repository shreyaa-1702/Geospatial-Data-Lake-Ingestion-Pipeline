"""
Configuration management for the Street View Pipeline.
Supports YAML files, environment variables, and CLI overrides.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

# Tile grid dimensions per zoom level (cols × rows)
ZOOM_GRID: Dict[int, Tuple[int, int]] = {
    0: (1, 1),      #   512 ×   512  px
    1: (2, 1),      #  1024 ×   512  px
    2: (4, 2),      #  2048 ×  1024  px
    3: (8, 4),      #  4096 ×  2048  px  ← default
    4: (16, 8),     #  8192 ×  4096  px
    5: (32, 16),    # 16384 ×  8192  px
}


@dataclass
class Config:
    # ── Authentication ─────────────────────────────────────────────────────
    # Optional but recommended.  Geocoding API + Street View Metadata API.
    # Metadata lookups are FREE on Google's free tier ($200/month credit).
    # If omitted, Nominatim (OpenStreetMap) geocoding + unofficial pano
    # lookup are used instead — works but is less reliable.
    google_api_key: Optional[str] = None

    # ── Panorama quality ───────────────────────────────────────────────────
    zoom_level: int = 3          # 0-5  (higher = larger file, more detail)
    tile_size: int = 512         # Pixels per tile (Google fixed at 512)
    image_quality: int = 95      # JPEG quality 1-100

    # ── Network ────────────────────────────────────────────────────────────
    max_retries: int = 3
    retry_delay_base: float = 1.0     # Exponential backoff base (seconds)
    request_timeout: int = 20
    concurrent_tiles: int = 8         # Parallel tile downloads per pano
    rate_limit_delay: float = 0.5     # Courtesy delay between locations

    # ── Street View search ─────────────────────────────────────────────────
    search_radius: int = 100     # Metres — nearest pano to geocoded point

    # ── Output ─────────────────────────────────────────────────────────────
    output_dir: str = "output"
    images_subdir: str = "images"
    csv_filename: str = "metadata.csv"
    failed_csv_filename: str = "failed.csv"

    # ── Logging ────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: Optional[str] = "pipeline.log"

    # ── Runtime flags ──────────────────────────────────────────────────────
    resume: bool = False    # Skip locations whose image already exists
    dry_run: bool = False   # Geocode + find pano only — no image download

    # ── Derived properties ─────────────────────────────────────────────────

    @property
    def images_dir(self) -> Path:
        return Path(self.output_dir) / self.images_subdir

    @property
    def csv_path(self) -> Path:
        return Path(self.output_dir) / self.csv_filename

    @property
    def failed_csv_path(self) -> Path:
        return Path(self.output_dir) / self.failed_csv_filename

    @property
    def zoom_grid(self) -> Tuple[int, int]:
        return ZOOM_GRID[self.zoom_level]

    @property
    def panorama_width(self) -> int:
        return self.zoom_grid[0] * self.tile_size

    @property
    def panorama_height(self) -> int:
        return self.zoom_grid[1] * self.tile_size

    # ── Constructors ───────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Config":
        """Load config from a YAML file, ignoring unknown keys."""
        with open(path, encoding="utf-8") as f:
            data: Dict[str, Any] = yaml.safe_load(f) or {}
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    @classmethod
    def from_env(cls) -> "Config":
        """Populate API key from environment variable GOOGLE_API_KEY."""
        cfg = cls()
        if api_key := os.environ.get("GOOGLE_API_KEY"):
            cfg.google_api_key = api_key
        return cfg

    def summary(self) -> str:
        cols, rows = self.zoom_grid
        lines = [
            "Pipeline Configuration",
            f"  API key  : {'✓ provided' if self.google_api_key else '✗ not set (using fallbacks)'}",
            f"  Zoom     : {self.zoom_level}  ({cols}×{rows} tiles → "
            f"{self.panorama_width}×{self.panorama_height} px)",
            f"  Output   : {self.images_dir}",
            f"  Resume   : {self.resume}",
            f"  Dry-run  : {self.dry_run}",
        ]
        return "\n".join(lines)

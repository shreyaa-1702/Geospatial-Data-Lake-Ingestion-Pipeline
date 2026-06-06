# Street View 360° Panorama Pipeline

Automated bulk downloader that converts a CSV of location names into stitched
360° equirectangular JPEG panoramas + a metadata CSV — ready for CV datasets,
digital twins, street-level mapping, and 3D reconstruction pipelines.

---

## Features

| | |
|---|---|
| 🗺️ **Geocoding** | Google Geocoding API or free Nominatim (OSM) fallback |
| 📍 **Pano lookup** | Official Street View Metadata API or unofficial fallback (no key required) |
| ⚡ **Concurrent tiles** | ThreadPoolExecutor — 8× faster than sequential |
| 🔁 **Retry + backoff** | Exponential backoff on 503 / timeout |
| 📄 **Metadata CSV** | name, street, lat/lng, pano_id, file size, date, status |
| ↷ **Resume mode** | Skip already-downloaded images on interrupted runs |
| 🔍 **Dry-run mode** | Geocode + find panos, write CSV, no image downloads |
| 🪵 **Structured logging** | Console + rotating log file |
| ⚙️ **YAML config** | All settings in one file; CLI flags override |

---

## Quick Start

### 1 — Install dependencies

```bash
pip install -r requirements.txt
```

### 2 — Prepare your locations CSV

```csv
name,address
India Gate,India Gate Rajpath New Delhi India
Gateway of India,Apollo Bandar Colaba Mumbai India
```

Columns:
- `name` *(required)* — Label for the image filename and CSV row.
- `address` *(required)* — Any geocodable address string.
- `pano_id` *(optional)* — Supply a known pano ID to skip geocoding + pano-lookup.

### 3 — Run

```bash
# No API key (uses free OpenStreetMap geocoding + unofficial pano lookup):
python main.py --locations locations.csv

# With Google API key (recommended — more reliable):
python main.py --locations locations.csv --api-key YOUR_KEY

# High-res (8192×4096 px), resumable:
python main.py --locations locations.csv --zoom 4 --resume
```

### 4 — Outputs

```
output/
├── images/
│   ├── India_Gate.jpg
│   ├── Gateway_of_India.jpg
│   └── ...
├── metadata.csv        ← all records
├── failed.csv          ← only failures (for reprocessing)
└── pipeline.log
```

---

## CLI Reference

```
python main.py [OPTIONS]

Required:
  -l, --locations FILE   Locations CSV or TXT file

Optional:
  -c, --config FILE      YAML config file (default: config.yaml)
  -k, --api-key KEY      Google Maps API key
  -z, --zoom 0-5         Zoom level (default: 3 = 4096×2048 px)
  -o, --output DIR       Output directory (default: ./output)
  -j, --concurrent N     Parallel tile downloads (default: 8)
      --radius METRES    Pano search radius (default: 100 m)
      --resume           Skip already-downloaded images
      --dry-run          Geocode + find panos only; no image downloads
      --log-level LEVEL  DEBUG / INFO / WARNING / ERROR
```

---

## Zoom Level Guide

| Level | Grid | Resolution | Tiles | Est. time / pano |
|-------|------|-----------|-------|-----------------|
| 0 | 1×1 | 512 × 512 | 1 | < 1s |
| 1 | 2×1 | 1024 × 512 | 2 | < 1s |
| 2 | 4×2 | 2048 × 1024 | 8 | ~2s |
| **3** | **8×4** | **4096 × 2048** | **32** | **~5s** |
| 4 | 16×8 | 8192 × 4096 | 128 | ~15s |
| 5 | 32×16 | 16384 × 8192 | 512 | ~60s |

---

## Google API Key (Optional but Recommended)

A key unlocks:
- **Google Geocoding API** — more accurate address resolution
- **Street View Metadata API** — reliable pano lookup (metadata is **FREE**)

### Getting a free key

1. Go to <https://console.cloud.google.com/>
2. Create a project → **Enable APIs** → enable:
   - **Maps JavaScript API** (covers Street View metadata)
   - **Geocoding API**
3. **Credentials** → Create API Key
4. Google gives $200/month free credit — enough for thousands of locations

### Setting the key

```bash
# Option A: CLI flag
python main.py --locations locations.csv --api-key YOUR_KEY

# Option B: Environment variable
export GOOGLE_API_KEY=YOUR_KEY
python main.py --locations locations.csv

# Option C: config.yaml
# google_api_key: "YOUR_KEY"
```

---

## Metadata CSV Schema

| Column | Description |
|--------|-------------|
| `name` | Location label (input) |
| `address` | Original input address |
| `formatted_address` | Geocoded canonical address |
| `street_name` | Reverse-geocoded road/street name |
| `latitude` | Pano latitude (precise) |
| `longitude` | Pano longitude (precise) |
| `pano_id` | Google Street View panorama ID |
| `image_filename` | e.g. `India_Gate.jpg` |
| `image_path` | Absolute path to saved image |
| `image_width` | Pixels |
| `image_height` | Pixels |
| `file_size_mb` | JPEG file size |
| `zoom_level` | Zoom level used |
| `capture_date` | When the pano was captured |
| `status` | `SUCCESS` / `GEOCODE_FAILED` / `NO_STREETVIEW` / `DOWNLOAD_FAILED` / `SKIPPED` / `DRY_RUN` |
| `error` | Error message (empty on success) |
| `processed_at` | ISO-8601 UTC timestamp |

---

## Project Structure

```
streetview_pipeline/
├── README.md
├── config.py                 # Settings dataclass (YAML / env / CLI)
├── config.yaml               # Default configuration
├── locations.csv             # Sample input
├── main.py                   # CLI entry point + orchestrator
├── requirements.txt
├── pipeline/
│   ├── __init__.py
│   ├── loader.py             # Read + validate locations CSV/TXT
│   ├── geocoder.py           # Address → lat/lng (Google or Nominatim)
│   ├── pano_finder.py        # lat/lng → pano_id (official or unofficial)
│   ├── downloader.py         # Concurrent tile download with retry
│   ├── stitcher.py           # Paste tiles into panorama image
│   └── exporter.py           # Write metadata + failed CSVs
└── utils/
    ├── __init__.py
    ├── logger.py             # Logging setup
    └── naming.py             # Safe filename generation


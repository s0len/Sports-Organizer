# Sports Organizer 2.0

A metadata-driven pipeline that normalizes sports releases from multiple publishers into pristine, Plex-friendly TV libraries. Every decision—season numbering, episode ordering, filenames, folder structure—comes from authoritative metadata, so you can switch sports or release groups with a simple config change.

This release rebuilds the project around remote YAML metadata (the same feeds used for Plex enrichment) and a declarative configuration file. No brittle regex trees, no per-sport shell scripts—just fetch metadata, map releases, and render the correct filenames.

## Highlights

- **Metadata first** – Pulls remote YAML (e.g. Formula 1 2025 feed) and converts it into normalized `Show → Season → Episode` objects, honoring official episode order, titles, and air dates.
- **Configurable per sport** – Each sport is defined in `sports.yaml`: metadata URL, matching rules, numbering overrides, and filename templates. Add or adjust sports without touching code.
- **Smart episode matching** – Regex capture groups + alias tables map release filenames to the right season and episode. Handles sprint weekends, pre/post shows, weekly sports, and more.
- **Deterministic filenames** – Render paths with templates like `{show_title} - S{season_number:02d}E{episode_number:02d} - {episode_title}` and auto-sanitize for the filesystem.
- **Caching & retries** – Metadata fetched once and cached with TTL (configurable per sport). Subsequent runs hit the cache unless expired.
- **Dry-run & polling modes** – Run once, run on a schedule, or use `--dry-run` to preview all moves before committing.
- **Docker-ready** – Ships with a lightweight Python 3.12 image. Mount `/config`, `/data/source`, `/data/destination`, `/data/cache` and go.

## Quick Start

1. **Create the config directory** on the host and copy the sample file:

   ```bash
   mkdir -p /path/to/config
   cp config/sports.sample.yaml /path/to/config/sports.yaml
   ```

2. **Adjust `sports.yaml`** for your environment—update `source_dir`, `destination_dir`, and add or tweak sports entries.

3. **Run via Docker**:

   ```bash
   docker run -d \
     --name sports-organizer \
     -v /path/to/config:/config \
     -v /path/to/downloads:/data/source \
     -v /path/to/library:/data/destination \
     -v /path/to/cache:/data/cache \
     ghcr.io/s0len/sports-organizer:latest
   ```

   Environment variables exposed by the container entrypoint:

   | Variable | Default | Purpose |
   |----------|---------|---------|
   | `SPORTS_ORGANIZER_CONFIG` | `/config/sports.yaml` | Config file path |
   | `SPORTS_ORGANIZER_SOURCE` | `/data/source` | Source directory to scan |
   | `SPORTS_ORGANIZER_DESTINATION` | `/data/destination` | Destination library root |
   | `SPORTS_ORGANIZER_CACHE` | `/data/cache` | Metadata + state cache |
   | `SPORTS_ORGANIZER_PROCESS_INTERVAL` | `0` | Poll interval seconds (0 = run once) |
   | `SPORTS_ORGANIZER_RUN_ONCE` | `true` | Set to `false` to loop |
   | `SPORTS_ORGANIZER_DRY_RUN` | `false` | Force dry-run |

4. **Preview without writing**:

   ```bash
   docker run --rm -it \
     -e SPORTS_ORGANIZER_DRY_RUN=true \
     -v /path/to/config:/config \
     -v /path/to/downloads:/data/source \
     -v /path/to/library:/data/destination \
     -v /path/to/cache:/data/cache \
     ghcr.io/s0len/sports-organizer:latest --dry-run --verbose
   ```

## Configuration Schema

`settings` apply globally, while each entry in `sports` customizes a league or release group.

```yaml
settings:
  source_dir: /data/source
  destination_dir: /data/destination
  cache_dir: /data/cache
  dry_run: false
  skip_existing: true
  poll_interval: 0
  link_mode: hardlink
  destination:
    root_template: "{show_title}"
    season_dir_template: "{season_number:02d} {season_title}"
    episode_template: "{show_title} - S{season_number:02d}E{episode_number:02d} - {episode_title}.{extension}"

sports:
  - id: formula1_2025
    name: Formula 1 2025
    metadata:
      url: https://raw.githubusercontent.com/s0len/meta-manager-config/refs/heads/main/metadata-files/formula1-2025.yaml
      show_key: Formula1 2025
      ttl_hours: 12
      season_overrides:
        Pre-Season Testing:
          season_number: 0
          round: 0
    source_globs:
      - "Formula.1.*"
    file_patterns:
      - regex: "(?i)^Formula\.1\.(?P<year>\d{4})\.Round(?P<round>\d{2})\.(?P<location>[^.]+)\.(?P<session>[^.]+)"
        season_selector:
          mode: round
          group: round
        episode_selector:
          group: session
        session_aliases:
          Race: ["Race"]
          Sprint: ["Sprint.Race", "Sprint"]
          Qualifying: ["Qualifying", "Quali"]
          Free Practice 1: ["FP1", "Free.Practice.1"]
          # ... more aliases
```

### Key Fields

- **`metadata.url`** – Remote YAML source. Sports that share metadata (e.g. separate seasons) just swap the URL.
- **`metadata.show_key`** – Which top-level show to load when the file contains more than one.
- **`season_overrides`** – Force season numbers or round mapping when metadata uses special titles (e.g. Pre-season Testing → round 0).
- **`file_patterns[].regex`** – Regex applied to filenames; capture groups become context variables (e.g. `{round}`, `{session}`) and drive season/episode selection.
- **`season_selector`** – How to map regex captures to seasons: by round number, metadata key, title, or sequence.
- **`session_aliases`** – Maps release session tokens to metadata episode titles. Add synonyms like `Sprint.Shootout`, `Weekend.Warm.Up`, `FP1`, etc.
- **Destination templates** – Optional overrides per sport or per pattern. Use any context key (`season_round`, `episode_title`, `originally_available`, etc.).

## How It Works

1. **Fetch metadata** – Download and cache the YAML (e.g. Formula 1 2025 feed[^1]). Parse into normalized objects with inferred season/round numbers.
2. **Scan downloads** – Walk the configured `source_dir`, filter by extensions/globs.
3. **Match releases** – Apply regex patterns to filenames. Season/episode selection uses capture groups plus metadata-driven alias tables.
4. **Render paths** – Build `{show}/{season}/{filename}` using templates, sanitize components, and enforce deterministic numbering.
5. **Link/copy** – Hardlink by default (configurable to copy or symlink). Skips already-processed files unless `skip_existing: false`.

## CLI Usage

```bash
python -m sports_organizer.cli --config /config/sports.yaml --dry-run --verbose
```

Flags:
- `--dry-run` – Preview actions without creating files.
- `--once` – Force a single pass even if `poll_interval` > 0.
- `--interval` – Override the poll interval.
- `--verbose` – Enable debug logging to inspect matching decisions.

## Tips

- **Multiple metadata feeds**: Add additional `sports` entries referencing different URLs (MotoGP, WEC, NBA, etc.).
- **Complex numbering**: Combine `season_overrides` and custom templates to produce formats like `S2024E2024-03-10` for weekly leagues.
- **Testing**: Use `--dry-run --verbose` to confirm every release hits the correct episode before enabling writes.
- **Caching**: The cache directory stores metadata JSON. Delete it to force a refetch when remote YAML changes.

## Roadmap

- Additional built-in pattern packs (MotoGP, IndyCar, NBA, NFL).
- Optional webhook/websocket triggers for continuous operation.
- Strategy plugins for bespoke numbering edge cases.

---

[^1]: Formula 1 2025 metadata feed – https://raw.githubusercontent.com/s0len/meta-manager-config/refs/heads/main/metadata-files/formula1-2025.yaml

# Sports Media Organizer

This container automatically organizes Formula and MotoGP racing media files into a structured Plex-friendly format. It monitors a source directory for new racing videos and organizes them by series, year, round, and session type.

## Features

- **Automatic Organization**: Monitors for new media files and organizes them into a proper structure
- **Series Support**:
  - Formula 1, Formula 2, Formula 3
  - MotoGP, Moto2, Moto3
- **Session Detection**: Automatically identifies and labels different session types:
  - Practice sessions (FP1, FP2, FP3)
  - Qualifying sessions
  - Sprint races
  - Main races
  - Pre/Post shows
- **Proper Naming**: Renames files with season and episode numbering for better Plex integration
- **Notification Support**: Optional Pushover notifications when files are processed

## Requirements

- Docker and Docker Compose
- MWR releases for Formula 1-3 or MotoGP/Moto2/Moto3 content
- Storage volumes for source and destination directories
  - Prefferably you mount the top directory and then provide source and dest from within that directory. That way we can use hardlinks which saves space and is superfast.
- Plex library requires to have the Agent set as "Personal Media"

## Installation

### Docker

```bash
docker run -d \
  --name=sports-organizer \
  -e SRC_DIR=/data/torrents/sport \
  -e DEST_DIR=/data/media/sport \
  -e PROCESS_INTERVAL=60 \
  -e PUSHOVER_NOTIFICATION=false \
  -v /path/to/downloads:/data/torrents/sport \
  -v /path/to/media:/data/media/sport \
  --restart unless-stopped \
  ghcr.io/username/sports-organizer:latest
```

### Docker Compose

```yaml
version: '3'
services:
  sports-organizer:
    image: ghcr.io/username/sports-organizer:latest
    container_name: sports-organizer
    environment:
      - SRC_DIR=/data/torrents/sport
      - DEST_DIR=/data/media/sport
      - PROCESS_INTERVAL=60
      - PUSHOVER_NOTIFICATION=false
      # Optional Pushover notification settings
      # - PUSHOVER_USER_KEY=your_user_key
      # - PUSHOVER_API_TOKEN=your_api_token
      - DEBUG=false
    volumes:
      - /path/to/downloads:/data/torrents/sport
      - /path/to/media:/data/media/sport
    restart: unless-stopped
```

### HelmRelease in Kubernetes

```yaml
---
# yaml-language-server: $schema=https://raw.githubusercontent.com/bjw-s/helm-charts/main/charts/other/app-template/schemas/helmrelease-helm-v2.schema.json
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: &app sports-organizer
  namespace: media
spec:
  interval: 15m
  chart:
    spec:
      chart: app-template
      version: 3.7.2
      sourceRef:
        kind: HelmRepository
        name: bjw-s
        namespace: flux-system
  maxHistory: 3
  install:
    remediation:
      retries: 3
  upgrade:
    cleanupOnFail: true
    remediation:
      strategy: rollback
      retries: 3

  values:
    controllers:
      main:
        type: deployment
        containers:
          app:
            image:
              repository: ghcr.io/s0len/sports-organizer
              tag: develop
            env:
              SRC_DIR: /data/torrents/sport
              DEST_DIR: /data/media/sport
              PROCESS_INTERVAL: 60
              PUSHOVER_NOTIFICATION: true
            envFrom:
              - secretRef:
                  name: sports-organizer-secret
            securityContext:
              privileged: false

    defaultPodOptions:
      automountServiceAccountToken: false
      enableServiceLinks: false
      securityContext:
        runAsUser: 568
        runAsGroup: 568
        runAsNonRoot: true
        fsGroup: 568

    persistence:
      data:
        type: nfs
        server: "${TRUENAS_IP}"
        path: /mnt/rust/data
        globalMounts:
          - path: /data
            readOnly: false

      tmp:
        type: emptyDir
        medium: Memory
```

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| SRC_DIR | Source directory to monitor for new files | /data/torrents/sport | Yes |
| DEST_DIR | Destination directory for organized files | /data/media/sport | Yes |
| PROCESS_INTERVAL | How often to check for new files (in seconds) | 60 | No |
| PUSHOVER_NOTIFICATION | Enable Pushover notifications | false | No |
| PUSHOVER_USER_KEY | Pushover user key | - | Only if notifications enabled |
| PUSHOVER_API_TOKEN | Pushover API token | - | Only if notifications enabled |
| DEBUG | Enable debug logging | false | No |

## File Organization Structure

Files are organized in the following structure:

```
/data/media/sport/
├── Formula 1 2023/
│   ├── 1 Bahrain/
│   │   ├── 1x3 Formula 1 Free Practice 1.mkv
│   │   ├── 1x4 Formula 1 Free Practice 2.mkv
│   │   ├── 1x5 Formula 1 Free Practice 3.mkv
│   │   ├── 1x7 Formula 1 Qualifying.mkv
│   │   └── 1x10 Formula 1 Race.mkv
│   └── 2 Saudi Arabia/
│       └── ...
├── MotoGP 2023/
│   ├── 1 Qatar/
│   │   ├── 1x1 MotoGP Free Practice 1.mkv
│   │   ├── 1x3 MotoGP Qualifying 1.mkv
│   │   ├── 1x5 MotoGP Sprint.mkv
│   │   └── 1x6 MotoGP Race.mkv
│   └── ...
└── ...
```

## Usage

1. Mount your download directory to the container's `/data/torrents/sport` path
2. Mount your media directory to the container's `/data/media/sport` path
3. The container will automatically scan for new files and organize them
4. Add your organized media directory to Plex as a TV Show library

## Troubleshooting

Check the container logs for any errors:

```bash
docker logs sports-organizer
```

Common issues:
- Incorrect permissions on source or destination directories
- Unsupported file naming format
- Insufficient disk space

## License

GPL v3

## Support

For issues, feature requests, or contributions, please visit the GitHub repository.

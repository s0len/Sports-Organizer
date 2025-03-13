FROM alpine:3.20

RUN apk update && apk add --no-cache bash inotify-tools coreutils findutils sed curl grep

COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

# Create a single data directory that will contain both source and destination
RUN mkdir -p /data/source /data/destination

# Set a single volume mount point for the parent directory
VOLUME ["/data"]

# Set environment variables
ENV SRC_DIR=/data/source
ENV DEST_DIR=/data/destination
ENV PUSHOVER_NOTIFICATION=false
ENV PROCESS_INTERVAL=60

ENTRYPOINT ["/entrypoint.sh"]
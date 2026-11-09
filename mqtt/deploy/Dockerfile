# pypixelgui MQTT bridge in a container (no GUI, no Qt).
# Build from the repository root:
#   docker build -f mqtt/deploy/Dockerfile -t pypixelgui-bridge .
# Bluetooth goes through the host's BlueZ over D-Bus – see docker-compose.yml.
FROM python:3.12-slim

WORKDIR /src
COPY mqtt/requirements-bridge.txt /src/requirements-bridge.txt
RUN pip install --no-cache-dir -r requirements-bridge.txt
COPY . /src
RUN pip install --no-cache-dir --no-deps /src /src/mqtt && rm -rf /src

# Settings (displays, presets, MQTT) live in the /config volume.
ENV XDG_CONFIG_HOME=/config
VOLUME /config
ENTRYPOINT ["pypixelgui-bridge"]
CMD ["run"]

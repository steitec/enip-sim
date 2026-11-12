# enip-sim in a container: the `drive` profile on EtherNet/IP TCP port 44818, with the
# control channel on 44819 for fault injection. Give another command to run a different
# profile or a mounted bench file.
#
# The control channel listens on all interfaces inside the container, because nothing
# could reach it otherwise. It has no authentication, so publish it on the host's
# loopback only.
#
#   docker run --rm -p 44818:44818 -p 127.0.0.1:44819:44819 ghcr.io/steitec/enip-sim
#   docker run --rm -p 44818:44818 -v ./bench.yaml:/bench/bench.yaml \
#       ghcr.io/steitec/enip-sim serve --bench /bench/bench.yaml --host 0.0.0.0 --control-host 0.0.0.0
FROM python:3.15-slim
LABEL org.opencontainers.image.source=https://github.com/steitec/enip-sim \
      org.opencontainers.image.description="Simulated EtherNet/IP (CIP) devices with behaviour and fault injection" \
      org.opencontainers.image.licenses=MIT

ENV PYTHONUNBUFFERED=1
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /src

RUN useradd --create-home --uid 10001 enip
USER enip
WORKDIR /home/enip

EXPOSE 44818 44819
# Healthy once the EtherNet/IP port accepts connections (the control channel may be off
# with --no-control). A service container in GitHub Actions waits for this.
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 44818), 2).close()"]
ENTRYPOINT ["enip-sim"]
CMD ["serve", "drive", "--host", "0.0.0.0", "--control-host", "0.0.0.0"]

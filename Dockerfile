FROM python:3.13 AS libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.13 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock ./README.md /app/
RUN VIRTUAL_ENV=/app/venv poetry install --no-root

FROM debian:trixie-slim
WORKDIR /app

# Install Python and networking dependencies for Tailscale
RUN apt update && \
    apt-get install -y python3.13 python3-pip ca-certificates iptables --fix-missing && \
    apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

# Copy Python dependencies from builder
COPY --from=libbuilder /app/venv/lib/python3.13/site-packages /app/

# Copy Tailscale binaries from the tailscale image
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscaled /app/tailscaled
COPY --from=docker.io/tailscale/tailscale:stable /usr/local/bin/tailscale /app/tailscale

# Create Tailscale directories
RUN mkdir -p /var/run/tailscale /var/cache/tailscale /var/lib/tailscale

# Copy application code
COPY . /app/

# Make start script executable
RUN chmod +x /app/start.sh

WORKDIR /app
CMD ["/app/start.sh"]
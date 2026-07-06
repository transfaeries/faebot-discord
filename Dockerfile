FROM python:3.13 AS libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.13 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock ./README.md /app/
RUN VIRTUAL_ENV=/app/venv poetry install --no-root

FROM debian:trixie-slim
WORKDIR /app

# Install Python (ca-certificates for HTTPS to Discord/OpenRouter)
RUN apt update && \
    apt-get install -y python3.13 python3-pip ca-certificates --fix-missing && \
    apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/

# Copy Python dependencies from builder
COPY --from=libbuilder /app/venv/lib/python3.13/site-packages /app/

# Copy application code
COPY . /app/

WORKDIR /app
CMD ["/usr/bin/python3.13", "/app/faediscord.py"]
FROM python:3.11 AS libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.11 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock ./README.md /app/
RUN VIRTUAL_ENV=/app/venv poetry install --no-root

FROM debian:bookworm-slim
WORKDIR /app
RUN apt update
RUN apt-get install -y python3.11 python3-pip --fix-missing
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/
COPY --from=libbuilder /app/venv/lib/python3.11/site-packages /app/
COPY ./faediscord.py ./admin_commands.py /app/
COPY prompts.txt /app/
WORKDIR /app
ENTRYPOINT ["/usr/bin/python3.11", "/app/faediscord.py"]

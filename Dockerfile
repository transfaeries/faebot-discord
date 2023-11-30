FROM python:3.10 as libbuilder
WORKDIR /app
RUN pip install poetry
RUN python3.10 -m venv /app/venv 
COPY ./pyproject.toml ./poetry.lock /app/
RUN VIRTUAL_ENV=/app/venv poetry install 

# FROM ubuntu:hirsute
FROM debian:bookworm-slim
WORKDIR /app
RUN apt update
RUN apt-get install -y python3.10 python3-pip --fix-missing
RUN apt-get clean autoclean && apt-get autoremove --yes && rm -rf /var/lib/{apt,dpkg,cache,log}/
COPY --from=libbuilder /app/venv/lib/python3.10/site-packages /app/
COPY ./faediscord.py /app/
COPY prompts.txt /app/
WORKDIR /app
ENTRYPOINT ["/usr/bin/python3.10", "/app/faediscord.py"]

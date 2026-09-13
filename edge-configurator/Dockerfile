FROM python:3.12-alpine

WORKDIR /app

COPY app ./app

RUN mkdir -p /config

CMD ["python", "-u", "app/configurator.py"]

FROM python:3.12-alpine

WORKDIR /app

COPY --chown=1000:1000 worker.py /app/worker.py
RUN chmod 644 /app/worker.py

USER 1000:1000

ENTRYPOINT ["python", "-u", "/app/worker.py"]

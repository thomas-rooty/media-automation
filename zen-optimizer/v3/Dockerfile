FROM python:3.12-alpine

WORKDIR /app

COPY --chown=1000:1000 monitor.py /app/monitor.py

RUN chmod 644 /app/monitor.py

USER 1000:1000

CMD ["python", "-u", "/app/monitor.py"]

# The Telegram app: one small image, runs anywhere Docker runs.
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 nutribot
WORKDIR /app

# Only needed for the Google-spreadsheet storage; the local-file storage needs nothing.
COPY api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY api ./api
COPY nutricore ./nutricore
COPY web ./web
COPY deploy/serve.py ./deploy/serve.py

USER nutribot
ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/').read(1)"
CMD ["python", "deploy/serve.py"]

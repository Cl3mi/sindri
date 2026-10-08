FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-deu \
        libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
# Exactly one worker: the extraction queue, the import recovery sweep and
# the housekeeping (retention + export-ZIP) thread all assume a single
# process holds the worker lock (docs/plans/2026-10-07-batch-async-review-design.md).
# Do not add --workers > 1 or run this behind a process manager that forks.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Sindri — Offline Drawing Balloon → Excel Extractor

Extracts numbered-balloon dimensions from an Intercable-template technical drawing PDF
into a reviewable inspection-sheet `.xlsx`. Fully offline, one container.

## Run (default, CPU/Tesseract)

    docker compose up

Open http://localhost:8000, upload your PDF, review/correct the table, download the .xlsx.

## Optional GPU vision-LLM OCR

Requires an NVIDIA GPU + NVIDIA Container Toolkit and a torch/transformers layer:

    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

Falls back to Tesseract automatically if no GPU is available.

## Tests

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    pytest -q

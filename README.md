# Sindri — Offline Drawing Balloon → Excel Extractor

Extracts numbered-balloon dimensions from an Intercable-template technical drawing PDF
into a reviewable inspection-sheet `.xlsx`. Fully offline, one container.

## Run (default, CPU/Tesseract)

    docker compose up

Open http://localhost:8000, upload your PDF, review/correct the table, download the .xlsx.

## Optional GPU vision-LLM OCR

Tesseract reads stacked GD&T tolerances, the Ø symbol and rotated dimensions
poorly. For higher accuracy, a local vision-LLM (Qwen2.5-VL) reads the crops
instead. It runs entirely on-machine — still offline — but needs an NVIDIA GPU
+ [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

**podman (NVIDIA CDI) — recommended on HPC hosts:**

    ./run-gpu.sh                       # builds Dockerfile.gpu, runs with --device nvidia.com/gpu=all
    # pin the full (non-MIG) GPU:  GPU=nvidia.com/gpu=1 ./run-gpu.sh
    # use docker instead:          ENGINE=docker ./run-gpu.sh

**docker with the nvidia runtime:**

    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build

> Note: compose's `deploy.resources.devices` GPU reservation is Docker-Swarm
> syntax that **podman-compose ignores** — under podman use `run-gpu.sh` (CDI
> device injection) instead, or the container will see no GPU and fall back to
> Tesseract.

Confirm the GPU is actually in use:

    curl localhost:8000/api/health        # want {"cuda": true, "ocr_backend_active": "VLMBackend"}

- This builds `Dockerfile.gpu` (torch/CUDA + transformers) and sets
  `OCR_BACKEND=vlm`.
- **First run downloads the model (~16 GB) from Hugging Face**, cached in the
  `hf_models` volume. Subsequent runs — and the extraction itself — are fully
  offline. To pre-seed for an air-gapped host, populate that volume once on a
  networked machine.
- Lower VRAM: set `VLM_MODEL_ID=Qwen/Qwen2.5-VL-3B-Instruct` in
  `docker-compose.gpu.yml`.
- If no GPU is detected at runtime, the app falls back to Tesseract
  automatically.

## Tests

    python -m venv .venv && . .venv/bin/activate
    pip install -r requirements.txt
    pytest -q

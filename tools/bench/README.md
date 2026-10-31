# tools/bench — M0-A model / hardware bake-off

Harness for Revision 2 §15 step 2 / milestone M0-A. It compares **candidate** VLM, LLM, and embedding models across the three D6 runtimes (`ollama`, `llamacpp`, `mlx`) and writes `docs/benchmarks/<date>.md`.

This is **not** the M3 production runtime. Adapters live only in this tool.

Hardware this repo actually has: **Apple M1, 8 GB RAM, macOS 14.5**, Python via `uv`, no full Xcode. The plan assumed 16 GB, so the candidate matrix has two RAM profiles:

| `--profile` | Who it is for | Model class |
| --- | --- | --- |
| `8gb` | the development Mac | ~3–4B quantised LLM/VLM + tiny embedder |
| `16gb` | the plan's original bake-off | ~7–8B Q4 LLM, ~7B VLM |

Every YAML row is marked `status: candidate`. Nothing here is a chosen default.

## Safety

- `watson-bench fetch` is the **only** command that downloads weights.
- `watson-bench run` installs a localhost-only socket guard. The only network it may use is a local Ollama server (`127.0.0.1:11434`). Missing models are skipped with a fetch hint; they are never pulled implicitly.

## One-or-two command path (8 GB Mac, Ollama)

Ollama is the path that does not need Xcode or Metal wheels. Close other apps first so the 3–4B model can load.

```bash
# from the repo root — once
uv sync --group bench
# Ollama.app must already be installed and running on localhost

# 1. download the 8 GB Ollama candidates (~7.5 GB, 10–20 min on a typical home link)
uv run watson-bench fetch --profile 8gb --runtime ollama

# 2. run the bake-off on the committed sample frames and write today's report
uv run watson-bench run --profile 8gb --runtime ollama --report
```

That writes:

- `tools/bench/results/8gb-<timestamp>/run.json`
- per-frame JSON under that run's `frames/`
- a blank hand-scoring CSV (`scoring.csv`)
- `docs/benchmarks/YYYY-MM-DD.md` (Recommendation section left for you)

### Expected duration and disk (8 GB / Ollama / sample frames)

| Step | Time on M1 8 GB | Disk |
| --- | --- | --- |
| `uv sync --group bench` | 1–2 min | ~50 MB venv extras |
| `fetch --profile 8gb --runtime ollama` | 10–20 min (network) | **~7.5 GB** (`qwen2.5vl:3b` 3.2 + `llama3.2:3b` 2.0 + `qwen2.5:3b` 2.0 + `nomic-embed-text` 0.27) |
| `run --profile 8gb --runtime ollama` on the 5 sample frames | **15–30 min** | results are tiny |

The harness unloads each Ollama model (`keep_alive=0`) before loading the next. Do not fetch the `16gb` profile on this Mac.

If you later point `--frames` at a 50-frame fixture directory, budget about **+8–15 min per VLM candidate**.

## Optional: MLX and llama.cpp on the same Mac

No full Xcode. Command Line Tools may still be required to build `llama-cpp-python`. Prefer a prebuilt Metal wheel if `uv` offers one.

```bash
# MLX + Hub downloads (Apple Silicon only)
uv sync --group bench --extra mlx --extra fetch
uv run watson-bench fetch --profile 8gb --runtime mlx
uv run watson-bench run --profile 8gb --runtime mlx --report

# llama.cpp / Metal — only if a wheel or CMAKE_ARGS build succeeds
CMAKE_ARGS="-DGGML_METAL=on" uv sync --group bench --extra llamacpp --extra fetch
uv run watson-bench fetch --profile 8gb --runtime llamacpp
uv run watson-bench run --profile 8gb --runtime llamacpp --report
```

Fetching **all three** 8 GB runtimes duplicates weights (Ollama store + `~/Library/Application Support/WATSON/models`). Budget **~15–20 GB** disk and **45–90 min** for the combined run.

A missing extra is skipped, not a crash:

```
skipped: mlx-lm is not installed. On the Mac: uv sync --group bench --extra mlx
```

## Commands

```bash
uv run watson-bench hardware
uv run watson-bench list --profile 8gb
uv run watson-bench list --profile 16gb --runtime ollama

# Linux CI / no models: fake runtime exercises metrics + report generation
uv run watson-bench run --profile 8gb --runtime fake --report /tmp/watson-bench.md
```

`run` defaults `--frames` to `tools/bench/samples/frames` (five committed synthetic PNGs + OCR JSON). `tools/bench/samples/generate.py` recreates them.

Regenerate a report from an existing run directory:

```bash
uv run watson-bench report --run-dir tools/bench/results/8gb-<timestamp>
```

## Metrics (plan §11.4 / M0-A)

Per candidate the harness records tokens/s, time per frame (VLM), time per call (LLM, structured-JSON understanding prompt), embedding texts/s, peak RSS, and load time. Hardware (chip, RAM, macOS version) is detected automatically.

Quality: each scored frame gets a JSON output plus a blank row on `scoring.csv` (`visible_ok`, `hallucination`, `useful_for_work_memory`, `notes`). The human scores up to 50 sampled frames.

## Candidate families (confirm on the Mac)

**8 GB**

- VLM: Qwen2.5-VL 3B (Ollama / MLX 4-bit / GGUF Q4_K_M)
- LLM: Llama 3.2 3B and Qwen2.5 3B (same three runtimes)
- Embeddings: nomic-embed-text (Ollama + GGUF) and MiniLM (MLX)

**16 GB**

- VLM: Qwen2.5-VL 7B
- LLM: Qwen2.5 7B and Llama 3.1 8B
- Embeddings: nomic-embed-text plus mxbai-embed-large (Ollama) / ModernBERT (MLX)

IDs and Hub filenames are in `tools/bench/candidates/*.yaml`. Treat Hub GGUF filenames as confirm-at-fetch-time.

## Layout

```
tools/bench/
  candidates/          per-role YAML (vlm, llm, embeddings)
  samples/frames/      committed synthetic PNG + OCR sidecars
  samples/generate.py  recreates the samples
  watson_bench/        harness package (CLI, adapters, report)
  results/             gitignored run output
```

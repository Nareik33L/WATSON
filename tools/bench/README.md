# tools/bench — M0-A model / hardware bake-off

Harness for Revision 2 §15 step 2 / milestone M0-A. It compares **candidate** VLM, LLM, and embedding models across the three D6 runtimes (`ollama`, `llamacpp`, `mlx`) and writes `docs/benchmarks/<date>.md`.

This is **not** the M3 production runtime. Adapters live only in this tool.

Hardware this repo actually has: **Apple M1, 8 GB RAM, macOS 14.5**, Python via `uv`, no full Xcode. The plan assumed 16 GB, so the candidate matrix has two RAM profiles:

| `--profile` | Who it is for | Model class |
| --- | --- | --- |
| `8gb` | the development Mac | ~3–4B quantised LLM/VLM + tiny embedder |
| `16gb` | the plan's original bake-off | ~7–8B Q4 LLM, ~7B VLM |

Every YAML row is marked `status: candidate`. Nothing here is a chosen default.

`watson-bench` is a uv workspace member (`tools/bench`). `uv sync` installs it editable, so `uv run watson-bench` uses this checkout — no `PYTHONPATH` and no `--no-sync`.

## Safety

- `watson-bench fetch` is the **only** command that downloads weights.
- `watson-bench run` installs a localhost-only socket guard covering `connect`, `connect_ex`, UDP `sendto`/`sendmsg`, and `getaddrinfo`/`gethostbyname`. The only allowed destination is loopback on the Ollama port (default `11434`). Missing models are skipped with a fetch hint; they are never pulled implicitly.
- `OLLAMA_HOST` accepts Ollama's own forms (`127.0.0.1:11434`, `localhost`, `http(s)://host:port`). An invalid or non-loopback value skips the ollama runtime instead of crashing.
- Default outputs are always in the checkout: `tools/bench/results/<profile>-<timestamp>/` and `docs/benchmarks/<date>.md`, even when the package is installed into `.venv`. Override with `--repo-root` / `WATSON_REPO_ROOT`.
- Per-candidate rows are appended to that run's `scoring.csv` (one shared sheet per run directory).

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

# A0 transcription (fake runtime + committed edge fixtures)
uv run watson-bench transcribe --profile 8gb --engines fake --sizes base --report /tmp/a0.md
```

`run` defaults `--frames` to `tools/bench/samples/frames` (five committed synthetic PNGs + OCR JSON). `tools/bench/samples/generate.py` recreates them. To hand-score a golden day, pass `--frames tests/fixtures/<name>` (PNG search recurses) and `--frame-list tests/fixtures/<name>/sample50.txt`.

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

## V1.1 A0 — transcription bake-off (Device Ops)

Harness for plan A5 / A0. `TranscriptionRuntime.transcribe(pcm16k, language_hint)` returns segments with `start_ms`, `end_ms`, `text`, `avg_logprob`, `no_speech_prob`, `compression_ratio`. Engines: `pywhispercpp`, `mlx-whisper`, `faster-whisper` (plus `fake` for Linux CI). Sizes: `base`, `small` on both RAM profiles (`tiny` is the macOS CI smoke only).

Weights live at `<models_dir>/<engine>/<size>/` for fetch, readiness, and load (not the Hugging Face `models--*` cache layout). Each engine/size pair is measured in a **spawned child** so peak RSS is that child's `ru_maxrss` and unload is process exit. `mlx-whisper` `load()` constructs the model into `ModelHolder` (so `load_time_s` is real) and `unload()` clears the holder; the child is belt-and-suspenders if a global leak remains. `--fetch-only` is the only network use and requires `WATSON_ALLOW_NETWORK=1`. Measured `transcribe` runs install the same loopback socket guard as M0-A (no Ollama port) and **force** `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`.

### Exact Mac commands (8 GB profile)

Close other apps first. From the repo root:

```bash
# 1. deps (bench + WER + the three engines; mlx-whisper is Apple Silicon only)
uv sync --group bench --extra transcribe --extra mlx-whisper --extra pywhispercpp --extra fetch

# 2. render A0 meeting fixtures (macOS `say` + `afconvert`; ~2–5 min)
uv run watson-fixturegen meetings render --all --out tests/fixtures/meetings

# 3. download base + small for each engine (~1.5–3 GB, 10–20 min on a home link)
WATSON_ALLOW_NETWORK=1 uv run watson-bench transcribe \
  --profile 8gb \
  --engines pywhispercpp,mlx-whisper,faster-whisper \
  --sizes base,small \
  --fetch-only

# 4. offline bake-off on the rendered fixtures; write today's report
uv run watson-bench transcribe \
  --profile 8gb \
  --engines pywhispercpp,mlx-whisper,faster-whisper \
  --sizes base,small \
  --fixtures tests/fixtures/meetings \
  --report
```

That writes:

- `tools/bench/results/transcribe-8gb-<timestamp>/run.json`
- `docs/benchmarks/YYYY-MM-DD-transcription.md` (Recommendation left blank)

16 GB profile is the same sizes; pass `--profile 16gb`. Do not fetch `medium` on the 8 GB Mac.

### Expected duration and disk (8 GB / A0)

| Step | Time on M1 8 GB | Disk |
| --- | --- | --- |
| `uv sync --group bench --extra transcribe --extra mlx-whisper --extra pywhispercpp --extra fetch` | 2–5 min | ~200–400 MB venv extras |
| `meetings render --all` | 2–5 min (`say` per line) | ~23 MB WAV + ~3 MB m4a for the 12-minute stand-up; edges are tiny |
| `--fetch-only` base+small × 3 engines | 10–20 min (network) | **~1.5–3 GB** under `~/Library/Application Support/WATSON/models` (faster-whisper CT2 + mlx snapshots + whisper.cpp ggml; some sizes overlap) |
| `transcribe --profile 8gb` on A0 fixtures | **15–40 min** | results are tiny |

Skip an engine that is not installed; the row records the reason. The macOS tiny smoke must **not** skip: fetch writes the canonical directory, then pytest asserts `skipped == False`, RTF > 0, and WER ≤ 50% on the `speech_short` `say` clip (WER/RTF are printed in the job log). Linux CI uses `--engines fake` plus the committed silence/tone/1-second/speech_short fixtures. The committed `speech_short` WAV is a synthetic tone placeholder (`synthetic: true`) so it cannot skew aggregate WER; per-fixture fake WER is still asserted.

### Metrics (plan A0 / A5)

Per engine and size: load time, real-time factor (wall / audio), WER vs the reference transcript (`jiwer`, speaker labels stripped), peak RSS, and hallucination on the silence and tone files (any non-empty text counts).

## Layout

```
tools/bench/
  candidates/          per-role YAML (vlm, llm, embeddings)
  samples/frames/      committed synthetic PNG + OCR sidecars
  samples/generate.py  recreates the samples
  watson_bench/        harness package (CLI, adapters, report, transcription/)
  results/             gitignored run output
```

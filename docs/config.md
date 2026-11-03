# W.A.T.S.O.N configuration

Runtime settings live in `config.toml` under the data directory
(`~/Library/Application Support/WATSON` or `WATSON_DATA_DIR`). The file
holds no secrets. Paths, capture interval, exclusions, and retention
placeholders are documented here.

## Exclusions

`[exclusions]` lists **merge** with the built-in defaults from plan §5.1
(password managers, Messages, FaceTime, Photos, Health, and the starter
window-title / denied-domain lists). Setting `bundle_ids` to your own
apps does **not** replace those defaults.

```toml
[exclusions]
bundle_ids = ["com.example.secret"]
```

`com.apple.Passwords`, 1Password, Bitwarden, and the rest of the starter
list stay excluded. Extra IDs are appended.

### Un-excluding a built-in default

`allowed_bundle_ids` is the **visible** override that removes a default
from the exclusion set. It is not silent:

- `load_config()` logs a warning for each built-in default it un-excludes
- `watson status` prints `exclusions.override.N: <bundle id>`
- the dashboard **Exclusion overrides** section lists the same IDs

```toml
[exclusions]
# FaceTime will be captured. Password managers stay excluded.
allowed_bundle_ids = ["com.apple.FaceTime"]
```

Do not put password-manager bundle IDs here unless you intend to store
those frames. There is no way to drop the defaults by replacing
`bundle_ids`.

## Intelligence runtime

`[intelligence]` selects the local adapter (plan D6 / ADR 0001). No cloud
endpoints; Ollama is loopback-only. Defaults: `runtime = "ollama"`,
`llm_model = "qwen2.5:3b"`, `embedding_model = "nomic-embed-text"`,
`ollama_host = "http://127.0.0.1:11434"`, `vlm_enabled = false`,
`context_window = 4096`, `keep_alive = "5m"`, `temperature = 0.0`,
`entity_similarity_threshold = 0.80`, `retrieve_top_k = 5`.

`[intelligence.ollama]` is the HTTP client: `timeout_s = 300` for 3B
(passed to `/api/chat` and `/api/embed`; 7–9B presets use 3600 s),
`num_ctx` (defaults to `context_window`, 4096),
`num_predict = 512` (sized to the understanding JSON schema; structured
repair retries use 256), and `unload_between_phases = true` unloads
resident models at sub-phase boundaries (understanding chat →
understanding embed → consolidation). Cluster, extract, and summary
share one chat load. At most one model is resident; chat completions
for a batch run before any resolver embeddings.
Chat and unload calls always send `temperature = 0` (chat), `num_ctx`,
`num_predict`, and `keep_alive`.

The understanding **v3** prompt (`prompt_version = understanding/v3`,
`PIPELINE_VERSION = 3`) caps numbered merged OCR at **3000 characters**
(~750 tokens). With the v3 system prompt, retrieved aliases, the JSON
schema, and `num_predict = 512`, that fits the default 4096-token window.
Existing `understanding/v1` / `pipeline_version = 1` and `understanding/v2`
/ `pipeline_version = 2` rows stay readable.

The code default chat model is `qwen2.5:3b` until the bake-off winner is
sent (plan §14, Kieran 21:53/21:55). The V1 default will be the largest
model that completes on 8 GB at strict precision ≥ 0.85. Select a
candidate without editing `config.toml`: `WATSON_LLM_MODEL=gemma2:9b` or
`watson eval --llm-model gemma2:9b` (also `llama3.1:8b`, `qwen2.5:7b`,
`llama3.2:3b`). That applies the per-model timeout / `num_ctx` preset.

`entity_similarity_threshold` is the embedding cosine above which a
free-text project/people candidate merges into an existing entity
(after a normalised alias miss). `retrieve_top_k` is how many
projects/people FTS5 name/alias hits are injected into the
understanding prompt.

```toml
[intelligence]
runtime = "ollama"
context_window = 4096

[intelligence.ollama]
timeout_s = 300
num_predict = 512
unload_between_phases = true
```

CI uses `runtime = "fake"` or `WATSON_INTELLIGENCE_RUNTIME=fake` (recorded
JSON, no socket). A missing runtime or model leaves understanding jobs
`pending` with `last_error` naming the model and `ollama pull …`.
`VisionRuntime.describe` is a stub (VLM off on 8 GB).

## Pipeline / day-end

`[pipeline]` bounds worker drains and the work-day clock (D12 / §3.7).
Defaults: `backlog_batch_size = 32`, `day_end = "18:30"` (local HH:MM),
`recovery_deadline_hours = 2`. After `day_end`, consolidation runs (or
catch-up on the next launch). Frames still `captured` wait until the
recovery deadline, then consolidation proceeds with a recorded gap.

```toml
[pipeline]
day_end = "18:30"
recovery_deadline_hours = 2
```

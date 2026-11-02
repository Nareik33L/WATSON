# WATSON configuration

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
`llm_model = "llama3.2:3b"`, `embedding_model = "nomic-embed-text"`,
`ollama_host = "http://127.0.0.1:11434"`, `vlm_enabled = false`,
`context_window = 4096`, `keep_alive = "5m"`, `temperature = 0.0`,
`entity_similarity_threshold = 0.80`, `retrieve_top_k = 5`.

`[intelligence.ollama]` is the HTTP client: `timeout_s = 300` (passed to
`/api/chat` and `/api/embed`; the urllib default of 120 s is too short for
llama3.2:3b on 8 GB), `num_ctx` (defaults to `context_window`, 4096),
`num_predict = 768` (sized to the understanding JSON schema), and
`unload_between_phases = true` unloads resident models at phase
boundaries (understanding → consolidation → summary), never mid-phase.
Chat options always send `temperature = 0`, `num_ctx`, and `num_predict`.

The understanding **v2** prompt (`prompt_version = understanding/v2`,
`PIPELINE_VERSION = 2`) caps numbered merged OCR at **4000 characters**
(~1000 tokens). With the v2 system prompt, retrieved aliases, the JSON
schema, and `num_predict = 768`, that fits the default 4096-token window.
Existing `understanding/v1` / `pipeline_version = 1` rows stay readable.

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
num_predict = 768
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

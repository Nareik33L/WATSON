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
`context_window = 8192`, `keep_alive = "5m"`, `temperature = 0.0`.

```toml
[intelligence]
runtime = "ollama"
```

CI uses `runtime = "fake"` or `WATSON_INTELLIGENCE_RUNTIME=fake` (recorded
JSON, no socket). A missing runtime or model leaves understanding jobs
`pending` with `last_error` naming the model and `ollama pull …`.
`VisionRuntime.describe` is a stub (VLM off on 8 GB).

# Benchmark reports

`watson-bench report` (and `watson-bench run --report`) write `YYYY-MM-DD.md` here.

Reports are produced by the M0-A harness. The **Recommendation** section starts blank; the 8 GB Mac run is filled in `2026-10-31.md`. Defaults, the Llama 4/5 request-as-outcome miss, and the deferred table (owner + milestone) are in `docs/decisions/0001-m0a-8gb-runtime-and-models.md`. Raw JSON lives in `tools/bench/results/8gb-20261031T143012Z/` (kept in git; other result dirs stay ignored).

`watson-bench transcribe --report` writes `YYYY-MM-DD-transcription.md` in the same format for the V1.1 A0 bake-off (load time, real-time factor, WER, peak RSS, silence/tone hallucination). Recommendation is again left blank for the Mac run.

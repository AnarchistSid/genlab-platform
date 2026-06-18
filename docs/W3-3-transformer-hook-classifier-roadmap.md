# W3.3 — Transformer-embedding hook classifier roadmap

## What W3.3 wants

Replace (or augment) the 8 hand-engineered regex features in
`hook_features.py` with semantic embeddings so the classifier can
discriminate *what* a hook is about (topic), not just *how* it's
structured (length, emoji count, question marks). The goal is a
classifier that flags `"This new AI model just rewrote everything"`
as gaming-news-irrelevant when scoring against a gaming bandit, even
though the regex features look identical to a real gaming hook.

This is **multi-day ML work**: feature engineering + training pipeline
+ model selection + serving. This roadmap breaks it into the four
shippable layers and ships **layer 1 (foundation)** in PR #336.

## Layer 1 — Embedding extraction (PR #336, this PR)

**Module**: `genlab_core.learning.hook_embeddings`

Adds a 3-tier embedding extractor:

1. **sentence-transformers** (preferred — semantic quality)
2. **scikit-learn HashingVectorizer** (fallback — no extra deps)
3. **zero vector** (fail-open — never crashes)

Tier auto-detected at module load; operator can pin via
`GENLAB_HOOK_EMBEDDING_TIER={sentence-transformers|tfidf|zero}`.

**Public API**:

```python
from genlab_core.learning.hook_embeddings import embed_hook
vec = embed_hook("Your hook text here")
# → numpy.ndarray, shape depends on active tier
```

**Why ship the foundation alone**: the extractor is self-contained,
fully tested (16 pins), zero downstream impact. It lets us decide
the tier at the operator level (`sentence-transformers` adds ~250MB
to the install tree — not free on a 4GB Hetzner VPS). The classifier
integration that USES these embeddings can move independently.

## Layer 2 — Feature concatenation (next PR)

**Module**: extend `hook_features.build_feature_vector()` with an
optional `include_embeddings: bool = False` flag that appends the
output of `embed_hook(text)` to the 8 hand-features. With the flag
off, the existing classifier and its serialised models keep working
unchanged. With the flag on, callers get a 264-d (8 + 256) or 392-d
(8 + 384) feature vector.

Backwards-compat contract: model files trained on 8-feature vectors
won't load against 264-feature classifiers and vice versa. Make the
flag part of the model's serialised metadata so a load-time check
catches drift.

## Layer 3 — Classifier swap or augmentation (the multi-day part)

Two paths to evaluate empirically:

**Path A — XGBoost on concatenated features.** Keep the existing
`hook_classifier.py` XGBoost setup, just feed it the 264-d/392-d
vectors. Pros: minimal pipeline change, model file compatibility
stays at "JSON-serialised XGBoost". Cons: XGBoost on 256-512 features
loses the gradient-boosting magic that worked on 8 highly-informative
hand features.

**Path B — Logistic regression on embeddings only.** Replace XGBoost
with sklearn's `LogisticRegression` over the embedding vector alone.
Pros: the embedding IS the feature engineering — concat with hand
features is redundant. Cons: throws away the existing model files;
operator has to retrain from scratch.

**Path A or B?** Run both offline against
`hook_training_data.load_all_examples()` and compare validation AUC.
The bigger empirical question is whether the embeddings actually add
signal — the 8 hand-features are surprisingly hard to beat for
short-text + small-dataset regimes (hooks are ≤ 60 chars; we have
~1000 training examples).

**Required ML work for layer 3**:

1. Validation split + AUC measurement on existing training data
2. Per-niche retraining (each niche has its own model)
3. Calibration check — bandit consumes scores as probabilities, so
   ensure the model's outputs are well-calibrated (Brier score or
   sklearn `calibration_curve`)
4. Inference latency check — the bandit ranks ~5-10 hooks per
   pipeline pass. sentence-transformers on a 22MB MiniLM is ~50ms
   per call CPU; TF-IDF is sub-1ms. The 50ms × 10 hooks = 500ms is
   probably acceptable but worth measuring before flipping prod.

## Layer 4 — Online integration

Once the new classifier is shown to improve validation AUC by ≥10%
over the hand-feature baseline:

1. Wire the new model into the pipeline at `score_and_filter.py`
   alongside the existing model
2. Shadow-mode: log both scores, use the OLD model for actual gating
   decisions for the first 2 weeks
3. Compare the two scores against actual engagement outcomes (reward_48h)
4. If the new model's correlation with reward_48h beats the old
   model by a clear margin, flip the flag

## Risk register

- **Cold-start**: tier auto-detection isn't deterministic across
  machines (sentence-transformers may or may not be installed).
  Mitigation: pin the tier in `publishing.yaml` per-niche, not just
  the env var.
- **Model file size**: sentence-transformers MiniLM is 22MB. Adding
  to git would bloat clones; ship it as a runtime download (which it
  is by default — HuggingFace pulls on first use). Operator must
  ensure prod has network egress to huggingface.co.
- **Latency**: first-call CPU latency is 1-3s for the model load.
  Pre-warm at process start in the pipeline `start_pipeline.py`
  entry point, OR accept the first-pipeline-run penalty.

## Status

- [x] Layer 1 — embedding extraction (PR #336)
- [ ] Layer 2 — feature concatenation
- [ ] Layer 3 — classifier swap/augmentation + offline AUC
- [ ] Layer 4 — online shadow integration + flip

Layer 1 unblocks Layer 2 immediately; Layer 3 needs a labelled
training-data pass that's separate research work (≥1 day to gather
+ split + train + report).

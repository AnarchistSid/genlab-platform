# R3: Predictive Content Selection (LinUCB)

**Goal**: Upgrade from context-free Thompson Sampling to LinUCB contextual bandit.
**Effort**: ~4h

## Problem

Current Thompson Sampling only tracks (alpha, beta) per arm — it can't learn that "gameplay clips perform better on weekends" or "reaction videos outperform trailers for anime." PendingFeedbackTask already has `bandit_context: Optional[dict]` but nothing populates it.

## Changes

### 1. Implement LinUCB in `genlab_core/learning/linucb.py`

```python
class LinUCBArm:
    """Single arm with d-dimensional context."""
    def __init__(self, d: int, alpha: float = 1.0):
        self.A = np.eye(d)      # d×d identity
        self.b = np.zeros(d)    # d-vector
        self.alpha = alpha      # exploration parameter

    def predict(self, x: np.ndarray) -> float:
        A_inv = np.linalg.inv(self.A)
        theta = A_inv @ self.b
        p = theta @ x + self.alpha * np.sqrt(x @ A_inv @ x)
        return float(p)

    def update(self, x: np.ndarray, reward: float):
        self.A += np.outer(x, x)
        self.b += reward * x

class LinUCBBandit:
    """Contextual bandit with LinUCB algorithm."""
    def __init__(self, arm_ids: list[str], d: int, alpha: float = 1.0):
        self.arms = {aid: LinUCBArm(d, alpha) for aid in arm_ids}

    def select(self, context: np.ndarray) -> str:
        scores = {aid: arm.predict(context) for aid, arm in self.arms.items()}
        return max(scores, key=scores.get)

    def update(self, arm_id: str, context: np.ndarray, reward: float):
        self.arms[arm_id].update(context, reward)
```

### 2. Define context feature vector (6 dimensions)

```python
def build_content_context(story: dict, niche_id: str) -> np.ndarray:
    return np.array([
        datetime.now().weekday() / 6.0,           # day_of_week [0,1]
        datetime.now().hour / 23.0,                 # hour_utc [0,1]
        {"youtube": 0, "reddit": 0.33, "rss": 0.66, "twitch": 1.0}
            .get(story.get("source_type", ""), 0.5),  # source_type
        min(story.get("duration_seconds", 30) / 60.0, 1.0),  # duration [0,1]
        min(story.get("view_velocity", 0) / 10000, 1.0),     # velocity [0,1]
        story.get("relevance_score", 0.5),                     # relevance [0,1]
    ])
```

### 3. Cold-start protection

If an arm has < 50 observations, fall back to Thompson Sampling (existing behavior). LinUCB activates per-arm once threshold is crossed.

### 4. Store context in PendingFeedbackTask

Populate `bandit_context` at publish time:
```python
task = PendingFeedbackTask(
    ...
    bandit_context={"features": context.tolist(), "arm_id": selected_arm}
)
```

### 5. Persist LinUCB state to SharePoint

Serialize A matrix and b vector as JSON arrays in the BanditArms SharePoint list (new columns: `A_matrix`, `b_vector`). Falls back to alpha/beta Thompson Sampling if these fields are empty.

## Files

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/learning/linucb.py` | NEW — LinUCB implementation |
| `genlab-core/src/genlab_core/learning/arm_loader.py` | Add LinUCB state load/save |
| `genlab-core/src/genlab_core/pipeline/stages/performance_learner.py` | Use LinUCB when available |
| `genlab-core/src/genlab_core/learning/metric_collector.py` | Pass context to bandit update |
| `genlab-core/tests/learning/test_linucb.py` | NEW — tests |

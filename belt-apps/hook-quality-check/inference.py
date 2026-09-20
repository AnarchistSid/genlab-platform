"""Score a short-form video hook and name the way it fails.

A hook is the first line of a Reel, Short or TikTok — the ~60 characters that
decide whether anyone watches the next second. Most bad hooks are not bad in
interesting ways. They fail in a handful of shapes that repeat forever, and
almost all of them are detectable without a model.

The shape that motivated this app is the quietest one: **the hook is just the
source video's title**. A writer that cannot find anything to say falls back to
echoing its input, and the result looks like a hook, passes a length check, and
is worthless. In the pipeline this came from, that failure sat in production
producing zero publishable videos while the surrounding counters all read as a
rendering problem. The log line that eventually explained it said:

    hook_equals_title: Hook is the title verbatim:
      'AI-generated: epic anime fight scene'
    hook_title_truncation: Hook is the title truncated at 60 chars with '...':
      'My Happy Marriage Special Episodes | Official Teaser | Ne...'

Both are trivially detectable **if you pass the title in**. That is the one
input people forget, and it is the one that catches the expensive bug — so
`source_title` is optional here, but the checks it unlocks are the point.

## The score is a ledger, not a verdict

Every check contributes a named, signed number to `score_components`. The score
is their sum, clamped to 0..1, and nothing else feeds it. You can always ask
"why 0.42?" and get an itemised answer.

This is deliberate. A single opaque quality float invites exactly one failure
mode: it drifts away from what it claims to measure, and because it is one
number nobody can see the drift. If a component here is wrong you can see which
one, ignore it, and re-weight downstream — which you cannot do with a float that
arrived from nowhere.

## What it does not do

No model, no network, no judgement about whether your *claim* is true or your
topic is interesting. It checks the craft of the line: is it a sentence, is it
specific, is it yours, is it the right length. A hook can score 1.0 and still be
about something nobody cares about.
"""
import logging
import re
import unicodedata
from typing import Dict, List, Optional

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("hook-quality-check")

# --- Tunables -----------------------------------------------------------
# Defaults follow the common short-form convention: a hook must fit on screen
# in one or two lines at a legible size, which lands around 60 characters.
DEFAULT_MAX_CHARS = 60
DEFAULT_MIN_CHARS = 15

# Prefixes that mean the language model answered *you* instead of the audience.
# These leak into production more often than anyone admits, usually when the
# source material was too thin to write from.
REFUSAL_PREFIXES = (
    "i need the", "i need more", "i cannot", "i can't", "i am unable",
    "i'm unable", "as an ai", "as a language model", "sorry, ", "sorry.",
    "unfortunately, i", "i don't have enough", "i do not have enough",
    "here is a hook", "here's a hook", "sure! here", "sure, here",
    "certainly! here", "okay, here", "note:", "hook:",
)

# Phrases that appear in every niche, describe nothing, and are therefore
# invisible in a feed. Not banned words -- banned *because they carry no
# information*. "Fans are going wild" is true of every video ever posted.
GENERIC_PHRASES = (
    "something big happened", "you won't believe", "you wont believe",
    "this changes everything", "the community is going wild",
    "fans are going wild", "fans are going crazy", "going viral right now",
    "need to see this", "needs to see this", "must see", "wait for it",
    "this is insane", "this is crazy", "mind blown", "mind-blowing",
    "game changer", "game-changer", "no more excuses", "cinema is back",
    "breaking the internet", "broke the internet", "everyone is talking about",
    "nobody is talking about", "this is why", "here's why", "heres why",
    "the truth about", "what really happened", "changed the game",
    "you need to know", "shocking truth", "will shock you", "is back and",
)

# Leftovers from a template that was never filled, or a prompt that leaked.
PLACEHOLDER_PATTERNS = (
    r"\[[a-z_ ]{2,}\]",      # [TITLE], [game name]
    r"\{[a-z_ ]{2,}\}",      # {title}
    r"<[a-z_ ]{2,}>",        # <insert hook>
    r"\bTODO\b", r"\bTBD\b", r"\bXXX\b", r"\bFIXME\b",
    r"\blorem ipsum\b", r"\binsert [a-z]+\b", r"\bplaceholder\b",
    r"\bexample text\b", r"\bAI-generated\b", r"\bai generated:\b",
)

# A hook needs a predicate. Without one it is a label, not a statement --
# "Grand Theft Auto V" is a title; "GTA V just broke its own record" is a hook.
# Curated rather than derived: a POS tagger is a heavy dependency for a check
# that only needs to answer "is there a verb-shaped token here at all".
VERBS = {
    "is", "are", "was", "were", "be", "been", "being", "am",
    "has", "have", "had", "do", "does", "did", "doing",
    "will", "would", "can", "could", "should", "must", "might", "may",
    "get", "gets", "got", "getting", "go", "goes", "going", "went", "gone",
    "make", "makes", "made", "making", "take", "takes", "took", "taking",
    "come", "comes", "came", "coming", "see", "sees", "saw", "seen",
    "know", "knows", "knew", "think", "thinks", "thought",
    "say", "says", "said", "tell", "tells", "told", "ask", "asks", "asked",
    "want", "wants", "wanted", "need", "needs", "needed",
    "use", "uses", "used", "using", "find", "finds", "found",
    "give", "gives", "gave", "put", "puts", "keep", "keeps", "kept",
    "let", "lets", "leave", "leaves", "left", "start", "starts", "started",
    "stop", "stops", "stopped", "turn", "turns", "turned",
    "show", "shows", "showed", "shown", "play", "plays", "played",
    "run", "runs", "ran", "running", "move", "moves", "moved",
    "win", "wins", "won", "lose", "loses", "lost", "beat", "beats",
    "break", "breaks", "broke", "broken", "hit", "hits", "drop", "drops",
    "dropped", "add", "adds", "added", "build", "builds", "built",
    "buy", "buys", "bought", "sell", "sells", "sold", "pay", "pays", "paid",
    "cost", "costs", "spend", "spends", "spent", "save", "saves", "saved",
    "cut", "cuts", "kill", "kills", "killed", "die", "dies", "died",
    "call", "calls", "called", "name", "names", "named",
    "launch", "launches", "launched", "release", "releases", "released",
    "announce", "announces", "announced", "reveal", "reveals", "revealed",
    "confirm", "confirms", "confirmed", "deny", "denies", "denied",
    "claim", "claims", "claimed", "admit", "admits", "admitted",
    "explain", "explains", "explained", "prove", "proves", "proved",
    "change", "changes", "changed", "fix", "fixes", "fixed",
    "miss", "misses", "missed", "catch", "catches", "caught",
    "throw", "throws", "threw", "score", "scores", "scored",
    "return", "returns", "returned", "arrive", "arrives", "arrived",
    "happen", "happens", "happened", "become", "becomes", "became",
    "look", "looks", "looked", "feel", "feels", "felt", "seem", "seems",
    "try", "tries", "tried", "help", "helps", "helped",
    "work", "works", "worked", "learn", "learns", "learned",
    "watch", "watches", "watched", "read", "reads", "write", "writes",
    "wrote", "sit", "sits", "sat", "stand", "stands", "stood",
    "hold", "holds", "held", "bring", "brings", "brought",
    "send", "sends", "sent", "meet", "meets", "met", "set", "sets",
    "grow", "grows", "grew", "rise", "rises", "rose", "fall", "falls", "fell",
    "jump", "jumps", "jumped", "skip", "skips", "skipped",
    "quit", "quits", "ban", "bans", "banned", "fire", "fires", "fired",
    "hire", "hires", "hired", "sue", "sues", "sued", "cancel", "cancels",
    "cancelled", "canceled", "delete", "deletes", "deleted",
    "steal", "steals", "stole", "stolen", "leak", "leaks", "leaked",
    "beats", "outsold", "outsells", "overtook", "overtakes",
    "landed", "lands", "crashed", "crashes", "melted", "melts",
    "refuses", "refused", "ignored", "ignores", "forgot", "forgets",
    "picked", "picks", "chose", "chooses", "swapped", "swaps",
    "stopped", "waited", "waits", "keeps", "stays", "stayed",
    "struck", "strike", "strikes", "notice", "notices", "noticed",
    "sank", "sink", "sinks", "swept", "sweep", "sweeps", "tore", "tears",
    "shot", "shoots", "shoot", "held", "spun", "spins", "flew", "flies",
    "drew", "draws", "draw", "blew", "blows", "sold", "bet", "bets",
    "led", "leads", "lead", "fought", "fights", "fight", "beat", "chased",
    "dodged", "traded", "signed", "signs", "benched", "ranked", "ranks",
    "topped", "tops", "edged", "edges", "clinched", "sealed", "denied",
    "banned", "fined", "sued", "hyped", "teased", "teases", "tease",
}

# Function words that look like proper nouns when a hook is title-cased or
# shouted. Excluded from the specificity bonus so "THE" cannot score as a name.
_NON_NAMES = {
    "the", "a", "an", "and", "or", "but", "if", "so", "then", "than",
    "this", "that", "these", "those", "here", "there", "what", "why",
    "how", "when", "where", "who", "which", "is", "are", "was", "were",
    "no", "not", "yes", "it", "its", "his", "her", "their", "your", "our",
    "you", "we", "they", "he", "she", "i", "me", "my", "just", "now",
    "new", "big", "best", "worst", "top", "of", "in", "on", "at", "to",
    "for", "with", "from", "by", "as", "up", "out", "off", "all", "one",
}

# Low-content tokens ignored when measuring how much of a hook came from the
# source title -- otherwise "the"/"of" inflate or deflate the overlap ratio
# depending on which side happens to use more of them.
_STOPWORDS = _NON_NAMES | {
    "into", "over", "under", "after", "before", "about", "against", "been",
    "will", "can", "has", "have", "had", "did", "does", "do", "s", "t",
}


# -ing words that are ordinary nouns, so they do not count as a predicate.
_ING_NOUNS = {
    "gaming", "boxing", "racing", "wrestling", "trading", "casting",
    "ranking", "rating", "opening", "ending", "meeting", "morning",
    "evening", "building", "training", "clothing", "everything",
    "something", "nothing", "anything", "king", "thing", "spring",
    "string", "swing", "wing", "ring", "bring", "during",
}


def _has_verb_signal(words):
    """True if any token looks like a predicate.

    Two passes. The curated set catches irregulars and copulas that no suffix
    rule can reach ("is", "won", "struck"). The suffix pass catches the long
    tail of regular forms without shipping a POS tagger for one boolean.

    Biased toward answering yes. A false "no verb" blocks a good hook, which
    is a worse outcome than letting one bare title through -- the length and
    echo checks will usually catch that anyway.
    """
    for w in words:
        if w in VERBS:
            return True
    for w in words:
        if len(w) >= 5 and w.endswith("ing") and w not in _ING_NOUNS:
            return True
        if len(w) >= 4 and w.endswith("ed"):
            return True
    return False


_WORD_RE = re.compile(r"[a-z0-9']+")
_ELLIPSIS = ("...", "…")


def _norm(text: str) -> str:
    """Casefold, strip accents and punctuation, collapse whitespace.

    Used only for *comparison* against the source title. Comparing raw strings
    would miss the common near-miss where a writer echoes the title but drops a
    colon or changes the dash style -- still an echo, still worthless.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall(text.casefold())


class Finding(BaseModel):
    code: str = Field(description="Stable machine-readable identifier for the problem")
    severity: str = Field(description="fail (do not publish) or warn (weak but shippable)")
    detail: str = Field(description="What was found, quoting the offending text")
    fix: str = Field(description="Concrete suggestion for what to do instead")


class ScoreComponent(BaseModel):
    name: str = Field(description="Which check produced this contribution")
    delta: float = Field(description="Signed contribution to the score")
    reason: str = Field(description="Why this check fired")


class HookItem(BaseModel):
    hook: str = Field(description="The hook text to evaluate")
    source_title: Optional[str] = Field(
        None,
        description=(
            "Title of the source video or article the hook was written from. "
            "Optional, but this is what unlocks the echo checks -- pass it."
        ),
    )
    label: Optional[str] = Field(
        None, description="Free-form identifier echoed back in the result (id, slug, filename)"
    )


class AppSetup(BaseAppSetup):
    max_chars: int = Field(
        default=DEFAULT_MAX_CHARS,
        description="Hard character ceiling. 60 suits IG/TikTok/Shorts overlays.",
    )
    min_chars: int = Field(
        default=DEFAULT_MIN_CHARS,
        description="Floor below which a hook is almost always a truncation artefact.",
    )
    strict: bool = Field(
        default=False,
        description="Promote every warn to fail. Use in CI where any weak hook should block.",
    )


class RunInput(BaseModel):
    hook: str = Field(description="The hook text to evaluate")
    source_title: Optional[str] = Field(
        None,
        description=(
            "Title of the source video/article. Optional but strongly recommended: "
            "the title-echo checks are the ones that catch silent writer failure."
        ),
    )


class RunOutput(BaseModel):
    verdict: str = Field(description="pass, warn or fail")
    score: float = Field(description="0.0-1.0, the clamped sum of score_components")
    hook: str = Field(description="The hook as evaluated, after whitespace normalisation")
    char_count: int = Field(description="Length in characters")
    word_count: int = Field(description="Length in words")
    findings: List[Finding] = Field(description="Every problem found, worst first")
    score_components: List[ScoreComponent] = Field(
        description="Itemised ledger. The score is the clamped sum of these deltas."
    )
    summary: str = Field(description="One-line human-readable result")


class BatchInput(BaseModel):
    items: List[HookItem] = Field(description="Hooks to evaluate in one call")


class BatchResult(BaseModel):
    label: Optional[str] = Field(None, description="Echoed from the input item")
    verdict: str = Field(description="pass, warn or fail")
    score: float = Field(description="0.0-1.0")
    hook: str = Field(description="The hook as evaluated")
    findings: List[Finding] = Field(description="Every problem found, worst first")


class BatchOutput(BaseModel):
    total: int = Field(description="Number of hooks evaluated")
    passed: int = Field(description="Count with verdict=pass")
    warned: int = Field(description="Count with verdict=warn")
    failed: int = Field(description="Count with verdict=fail")
    mean_score: float = Field(description="Mean score across all hooks")
    top_failure_codes: Dict[str, int] = Field(
        description="Finding code -> occurrence count, so you can see the dominant failure shape"
    )
    results: List[BatchResult] = Field(description="Per-hook results, worst score first")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.max_chars = max(1, config.max_chars)
        self.min_chars = max(0, config.min_chars)
        self.strict = config.strict
        logger.info(
            "hook-quality-check ready (max_chars=%d min_chars=%d strict=%s)",
            self.max_chars, self.min_chars, self.strict,
        )

    # -- checks ----------------------------------------------------------
    def _evaluate(self, hook: str, source_title: Optional[str]):
        """Return (findings, components). Pure -- no state, no I/O."""
        findings: List[Finding] = []
        components: List[ScoreComponent] = []

        clean = re.sub(r"\s+", " ", (hook or "")).strip()
        low = clean.casefold()
        n_chars = len(clean)
        words = _tokens(clean)
        n_words = len(words)

        def fail(code, detail, fix):
            findings.append(Finding(code=code, severity="fail", detail=detail, fix=fix))

        def warn(code, detail, fix):
            findings.append(Finding(code=code, severity="warn", detail=detail, fix=fix))

        def add(name, delta, reason):
            components.append(ScoreComponent(name=name, delta=round(delta, 3), reason=reason))

        add("base", 1.0, "every hook starts at 1.0; checks below subtract")

        # 1. Empty ---------------------------------------------------------
        if not clean:
            fail("empty", "Hook is empty or whitespace only.",
                 "Write a hook. An empty hook means the writer had no usable input.")
            add("empty", -1.0, "no text at all")
            return findings, components

        # 2. Model spoke to you, not the audience --------------------------
        for pref in REFUSAL_PREFIXES:
            if low.startswith(pref):
                fail("llm_refusal_preamble",
                     f"Hook opens with {pref!r} -- this is the model addressing the "
                     f"operator, not the audience: {clean[:70]!r}",
                     "Usually means the source material was too thin to write from. "
                     "Fix the input context rather than the hook.")
                add("llm_refusal_preamble", -0.9, f"opens with {pref!r}")
                break

        # 3. Title echo ----------------------------------------------------
        # The expensive failure. Only reachable when source_title is supplied.
        if source_title:
            nh, nt = _norm(clean), _norm(source_title)
            if nh and nt:
                if nh == nt:
                    fail("hook_equals_title",
                         f"Hook is the source title verbatim: {clean[:70]!r}",
                         "The writer produced no hook. Give it real story context, "
                         "or drop the source -- a title is not a hook.")
                    add("hook_equals_title", -0.9, "identical to source title after normalisation")
                elif clean.endswith(_ELLIPSIS) and nt.startswith(_norm(clean.rstrip(".… "))):
                    fail("hook_title_truncation",
                         f"Hook is the title cut off at a character limit: {clean[:70]!r}",
                         "Same root cause as hook_equals_title -- the title was echoed and "
                         "then truncated by the length cap. Fix the writer's input.")
                    add("hook_title_truncation", -0.9, "title prefix ending in an ellipsis")
                elif len(nh) >= 20 and nt.startswith(nh):
                    fail("hook_title_prefix",
                         f"Hook is a leading slice of the title with no ellipsis: {clean[:70]!r}",
                         "The quiet variant of truncation -- no '...' to give it away. "
                         "Still an echo.")
                    add("hook_title_prefix", -0.8, "normalised hook is a prefix of the title")
                else:
                    ht = set(_tokens(nh)) - _STOPWORDS
                    tt = set(_tokens(nt)) - _STOPWORDS
                    if ht and tt:
                        overlap = len(ht & tt) / len(ht)
                        if overlap >= 0.80:
                            warn("hook_title_overlap",
                                 f"{overlap:.0%} of the hook's words come from the title -- "
                                 f"reworded echo rather than a new line.",
                                 "Say something the title does not: a number, an outcome, "
                                 "a consequence.")
                            add("hook_title_overlap", -0.35,
                                f"{overlap:.0%} token overlap with title")
        else:
            add("no_source_title", 0.0,
                "source_title not supplied -- echo checks skipped, score is optimistic")

        # 4. Length --------------------------------------------------------
        if n_chars > self.max_chars:
            over = n_chars - self.max_chars
            fail("too_long",
                 f"{n_chars} characters, {over} over the {self.max_chars} limit.",
                 f"Cut {over} characters. Drop the setup clause and lead with the claim.")
            add("too_long", -min(0.6, 0.04 * over), f"{over} chars over limit")
        elif n_chars < self.min_chars:
            fail("too_short",
                 f"Only {n_chars} characters -- below the {self.min_chars} floor, which "
                 f"usually means truncation or a placeholder: {clean!r}",
                 "Check whether something upstream cut this off.")
            add("too_short", -0.5, f"{n_chars} chars, under floor")

        # 5. Placeholders --------------------------------------------------
        for pat in PLACEHOLDER_PATTERNS:
            m = re.search(pat, clean, re.IGNORECASE)
            if m:
                fail("placeholder_leftover",
                     f"Contains unfilled template text {m.group(0)!r}.",
                     "A template never got its substitution. This would publish as-is.")
                add("placeholder_leftover", -0.8, f"matched {m.group(0)!r}")
                break

        # 6. Generic phrasing ----------------------------------------------
        hits = [p for p in GENERIC_PHRASES if p in low]
        if hits:
            warn("generic_phrasing",
                 f"Uses stock phrasing that describes any video: {', '.join(repr(h) for h in hits[:3])}",
                 "Replace with the specific fact. Not 'this is insane' but what happened.")
            add("generic_phrasing", -min(0.45, 0.2 * len(hits)),
                f"{len(hits)} generic phrase(s)")

        # 7. Predicate -----------------------------------------------------
        has_verb = _has_verb_signal(words)
        if not has_verb and n_words >= 2:
            fail("bare_title_no_verb",
                 f"No verb -- this is a label, not a statement: {clean!r}",
                 "Add what happened. 'Grand Theft Auto V' -> 'GTA V just broke its own record'.")
            add("bare_title_no_verb", -0.6, "no recognised verb token")
        elif has_verb:
            add("has_verb", 0.05, "contains a predicate")

        # 8. Shouting ------------------------------------------------------
        letters = [c for c in clean if c.isalpha()]
        if len(letters) >= 10:
            caps = sum(1 for c in letters if c.isupper()) / len(letters)
            if caps >= 0.7:
                warn("all_caps",
                     f"{caps:.0%} uppercase -- reads as shouting and is harder to scan.",
                     "Sentence case. Reserve caps for one word you actually want to stress.")
                add("all_caps", -0.2, f"{caps:.0%} of letters uppercase")

        # 9. Specificity ---------------------------------------------------
        # Positive signals. A hook that survives every check above but names
        # nothing concrete is not wrong, just forgettable.
        has_number = bool(re.search(r"\d", clean))
        title_tokens = set(_tokens(_norm(source_title))) if source_title else set()
        # Skip the first token (sentence case capitalises it regardless),
        # anything ALL-CAPS (that is shouting, not a name), function words,
        # and anything already present in the source title.
        raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9'\u2019]*", clean)
        proper = [
            w for w in raw_tokens[1:]
            if w[0].isupper()
            and len(w) >= 3           # 'Ne' from a truncated title is not a name
            and not w.isupper()
            and w.casefold() not in _NON_NAMES
            and w.casefold() not in title_tokens
        ]
        if has_number:
            add("has_number", 0.08, "contains a number -- concrete and scannable")
        if proper:
            add("has_proper_noun", 0.07,
                f"names something the title does not: {proper[0]!r}")
        if not has_number and not proper:
            warn("low_specificity",
                 "No number and no proper noun -- nothing concrete for a viewer to grab.",
                 "Name the thing. A score, a price, a player, a version number.")
            add("low_specificity", -0.15, "no number, no distinct proper noun")

        # 10. Shape --------------------------------------------------------
        if clean.endswith("?"):
            add("question_form", 0.04, "question form -- opens a curiosity gap")
        if n_words == 1:
            warn("single_word",
                 f"One word: {clean!r}. Almost never enough to earn a second of watch time.",
                 "Give it a subject and a predicate.")
            add("single_word", -0.3, "one token")

        return findings, components

    def _finalise(self, hook: str, findings: List[Finding], components: List[ScoreComponent]):
        score = max(0.0, min(1.0, sum(c.delta for c in components)))
        sev = {f.severity for f in findings}
        if self.strict and sev:
            verdict = "fail"
        elif "fail" in sev:
            verdict = "fail"
        elif "warn" in sev:
            verdict = "warn"
        else:
            verdict = "pass"
        # Worst first, so the caller reads the blocking problem before the nits.
        order = {"fail": 0, "warn": 1}
        findings.sort(key=lambda f: order.get(f.severity, 2))
        return round(score, 3), verdict

    # -- functions -------------------------------------------------------
    async def run(self, input_data: RunInput) -> RunOutput:
        """Evaluate a single hook."""
        clean = re.sub(r"\s+", " ", (input_data.hook or "")).strip()
        logger.info("checking hook (%d chars, title=%s)",
                    len(clean), "yes" if input_data.source_title else "no")

        findings, components = self._evaluate(clean, input_data.source_title)
        score, verdict = self._finalise(clean, findings, components)

        if verdict == "pass":
            summary = f"pass ({score:.2f}) — no problems found"
        else:
            codes = ", ".join(f.code for f in findings[:3])
            summary = f"{verdict} ({score:.2f}) — {len(findings)} finding(s): {codes}"
        logger.info("verdict=%s score=%.2f findings=%d", verdict, score, len(findings))

        return RunOutput(
            verdict=verdict,
            score=score,
            hook=clean,
            char_count=len(clean),
            word_count=len(_tokens(clean)),
            findings=findings,
            score_components=components,
            summary=summary,
        )

    async def check_batch(self, input_data: BatchInput) -> BatchOutput:
        """Evaluate many hooks and report the dominant failure shape.

        More useful than the single check when auditing a backlog: the
        `top_failure_codes` tally is what tells you whether you have sixty
        separate writing problems or one broken writer.
        """
        logger.info("batch checking %d hook(s)", len(input_data.items))
        results: List[BatchResult] = []
        tally: Dict[str, int] = {}
        total_score = 0.0

        for item in input_data.items:
            clean = re.sub(r"\s+", " ", (item.hook or "")).strip()
            findings, components = self._evaluate(clean, item.source_title)
            score, verdict = self._finalise(clean, findings, components)
            total_score += score
            for f in findings:
                tally[f.code] = tally.get(f.code, 0) + 1
            results.append(BatchResult(
                label=item.label, verdict=verdict, score=score,
                hook=clean, findings=findings,
            ))

        results.sort(key=lambda r: r.score)
        n = len(results) or 1
        passed = sum(1 for r in results if r.verdict == "pass")
        warned = sum(1 for r in results if r.verdict == "warn")
        failed = sum(1 for r in results if r.verdict == "fail")
        tally = dict(sorted(tally.items(), key=lambda kv: -kv[1]))

        if failed and tally:
            dominant = next(iter(tally))
            tail = f"; dominant failure: {dominant} ({tally[dominant]}x)"
        else:
            tail = ""
        summary = (
            f"{len(results)} hook(s): {passed} pass, {warned} warn, {failed} fail; "
            f"mean score {total_score / n:.2f}{tail}"
        )
        logger.info("%s", summary)

        return BatchOutput(
            total=len(results), passed=passed, warned=warned, failed=failed,
            mean_score=round(total_score / n, 3),
            top_failure_codes=tally, results=results, summary=summary,
        )

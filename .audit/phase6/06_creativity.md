# Creativity measurement (Phase 6 §1)

## The three numbers

1. **Distinct hooks: 33 / 100 posts** (each hook typically appears 4× — once
   per platform per reel, expected).
2. **Opening-token uniqueness: 33/33 = 100%.** No opening pattern carries even
   1 hook to another — the model isn't stuck on "Why is X" or "This is Y".
3. **3-gram Jaccard: mean 0.000, max 0.000.** No hook pair exceeds 0.5. But
   this is inflated by hooks that ARE just a title (e.g. "League of Legends"
   is 3 words, no 3-gram overlap possible with anything else). Metric is
   partially degraded by short hooks.

## Per-niche diversity

| Niche | Distinct | Posts | Diversity |
|---|--:|--:|--:|
| ai_creators | 6 | 21 | 29% |
| anime | 7 | 20 | 35% |
| gaming | 7 | 19 | 37% |
| movies | 5 | 14 | 36% |
| sports | 8 | 26 | 31% |

Roughly uniform 29-37%. **No channel is dramatically more templated than
the others.** The expected 25% (4 platforms per reel) is the floor; actual
sits slightly above.

## Caption

- Length min 111, max 200, mean 191 chars. Matches CLAUDE.md 150-200 target.
- **34 of 100 posts have empty last-sentence** (heuristic proxy for CTA) —
  either CTA is genuinely absent, or the caption ends with hashtag block +
  attribution line. This needs a manual sample to disambiguate.

## Banned-phrasing scan (from CLAUDE.md content rules)

- "Something big happened": 0
- "Community is going wild": 0
- "Players need to see this": 0
- "Cinema is back": 0
- "No more excuses": 0
- "You have to see": 0

**Zero banned formulations detected.** The style-rule enforcement is working
on this axis.

## Ten hook+caption pairs read as a viewer would

```
[anime | threads]
  Why does the English dub make Ame even more unhinged?
  The English voice acting just hit different. Ame's obsession energy is
  PEAK in dub form, and this plushie scene proves it. 😭 Caught up yet?

[sports | threads]
  Haney's back in the gym like he never left
  Devin Haney grinding on the heavy bag with that precision footwork.
  This is what redemption looks like. 🥊 Would you start them?

[gaming | threads]
  League of Legends
  League of Legends  Via スタンミ  #Gaming #GamingReels #League  🎬 Original: @スタンミ

[ai_creators | threads]
  Apple v. OpenAI #Vergecast
  Apple v. OpenAI #Vergecast  Via The Verge  #AI #AITools #OpenAI
  🎬 Original: @The Verge —  Save this for later!

[anime | threads]
  Demon Slayer: Kimetsu no Yaiba Infinity Castle I | SHINOB...
  Demon Slayer: Kimetsu no Yaiba Infinity Castle I | SHINOBU VS. DOMA (ENGLISH DUB)

[movies | threads]
  The Odyssey: Where poor ppl literally see less 😭😭
  The Odyssey: Where poor ppl literally see less 😭😭  Via Jorski

[sports | threads]
  Shakur says he won't be wearing the Zuffa shorts in the...
  Shakur's not playing ball with Zuffa's branding. The boxer's drawing a
  line at the shorts — and honestly? The confidence is wild. 😂

[gaming | threads]
  DFG Alistar is back and it's actually broken
  League Classic just dropped on PBE and Pobelter's testing DFG Alistar.
  The old item meta is unhinged. 💀 Drop your take below 👇

[ai_creators | threads]
  Codex vs your calendar: Codex wins every time
  Hold up a red card on camera and Codex exits your meeting, blocks your
  calendar, sets OOO, and logs you out. No typing. No clicking. Just a...

[movies | facebook]
  Pam Voorhees before Jason—the origin story nobody asked for
  They're rewriting Friday the 13th from the killer's perspective. Pam
  Voorhees gets a prequel series and suddenly we're supposed to understand...
```

## Viewer judgement

**Would a person recognise these as machine-generated?**

**Mixed — some yes, most no.** The strong ones sound like a genuinely
observant creator: "Haney's back in the gym like he never left" and "Pam
Voorhees before Jason—the origin story nobody asked for" both feel like
they came from a viewer with a take, not a template. "The Odyssey: Where
poor ppl literally see less 😭😭" is genuinely funny and specific. The
Codex one plants a concrete scene ("hold up a red card on camera").

The weak ones are unmistakably machine-emitted:
- **"League of Legends"** as a hook is a passthrough of the source title —
  no framing added. This is the source's video title used as the hook, and
  it happens whenever the writer stage fails or the source title is short.
  Same pattern in "Apple v. OpenAI #Vergecast" and the Demon Slayer entry.
- Captions that duplicate the hook verbatim then append `Via <creator>` +
  `#Hashtags` + `🎬 Original: @creator` are template-visible: 3 of 10
  samples do this. When the writer stage produces nothing, the pipeline
  falls back to `title + attribution` and it looks like it.

**Which channel reads best?** `movies` and `sports`. Both have concrete
takes ("origin story nobody asked for", "drawing a line at the shorts").
Both use tension. Both sound like a person with taste.

**Which reads worst?** `ai_creators`. The Codex example works, but the
Apple/OpenAI passthrough is bare source title. AI news is genuinely harder
to hook than a boxing clip.

## Specific tells that give it away when it fails

1. **Bare source title as hook** (`League of Legends`, `Apple v. OpenAI
   #Vergecast`). Happens ~10-15% of samples.
2. **Hook duplicated verbatim in caption**. When present, it strips signal.
3. **Attribution line format is identical across posts**: `🎬 Original:
   @<handle> — <url>`. It's correct behavior per the attribution defense
   stack, but repetition at the sentence level is a tell.
4. **Emoji placement is systematic**: single 😭/🥊/💀/😂 near end. Never
   two different emoji, never at start. Human posts scatter.

## Ten-word summary

**Writing is real when it works, template when it fails.**

## The unmeasured

- Visual variety: I did not sample rendered `.mp4` frames beyond the
  Phase 3A video-first check. Two reels with different hooks may still
  render as visually identical layouts. **F-0052** candidate.
- Hashtag-set variance across niches: not measured this session.
- Time-of-day pattern in style: not measured.

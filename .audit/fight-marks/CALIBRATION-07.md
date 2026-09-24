# CALIBRATION-07 — PEAK-10: the window holds, and where trending sound isn't

## §1 The window is the content

PEAK-09 shortened Tanjiro's marked window by four seconds so its impact would
reach the generator's drop. `fit_bed` replaces that: the reel length is an
INPUT the fitter cannot reassign (pinned), and the track adapts in cost order
— stretch ≤ ±4%, then trim the intro or pre-roll, then loop a bar-aligned
section when the track runs out.

Fitting all four **full** marked windows:

| fight | wind-up | drop | stretch | trim | pre-roll | loops |
|---|---|---|---|---|---|---|
| zoro_vs_king | 9.20 s | 6.75 s | −4.0% | 0.00 s | 2.17 s | 0 |
| luffy_vs_kaido | 13.80 s | 13.45 s | −2.5% | 0.00 s | **0.00 s** | 0 |
| deku_vs_overhaul | 13.50 s | 12.75 s | −4.0% | 0.00 s | 0.22 s | 0 |
| tanjiro_vs_akaza | 18.20 s | 16.55 s | −4.0% | 0.00 s | 0.96 s | 0 |

The looping path is not exercised by these four and is pinned rather than
left untested.

### Two collisions the assertion found, both real

* **The beat grid versus the mark.** Snapping cuts to the grid pulled Luffy's
  last shot to 76.887 s against a mark of 77.0. Both are hard constraints and
  they meet at the boundary. §1 says the mark wins, so interior cuts snap and
  the window's own edges are locked.
* **The print gate versus the mark.** PEAK-08 §3 drops a shot whose frames
  carry somebody else's subtitle; on Deku that shot is *inside* the marked
  window, so §1 and §3 cannot both hold. The print gate wins, because no edit
  can use those frames — but it is a different thing from fitting the fight
  to a drop, and Zoro's window already carries such a trim, accepted in
  PEAK-08. Every excused span is now recorded per reel rather than inferred
  from a length mismatch.

## §3 The tempo gate could not fail, and the packet's premise was the same bug

The packet reports that three of four v3 tracks failed a ±5% tempo check.
Measured: **all four v3 beds are at the tempo they asked for.** Two detector
readings say otherwise and both are artefacts.

| fight | asked | 120–180 band | 60–200 band | intermediate-beat ratio |
|---|---|---|---|---|
| zoro_vs_king | 148 | 148.0 | 74.0 | 1.06 |
| luffy_vs_kaido | 148 | 148.0 | 74.0 | 1.38 |
| deku_vs_overhaul | 140 | 140.0 | 70.0 | 0.89 |
| tanjiro_vs_akaza | 150 | 150.0 | 75.0 | 1.35 |

The 120–180 search **cannot express** a half-tempo generation, so a gate built
on it never fails — the third such meter in this arc after sub-share
(power vs magnitude) and the true-peak ceiling. Widening the band does not
resolve an octave, it moves where the ambiguity lands: a flux fit scores mean
onset strength at sampled positions, so halving the tempo can score *better*
by sampling the stronger subset.

What resolves it is asking whether the **intermediate beats carry onsets**.
At 0.89–1.38 they are as strong as the others, so the tracks are at the ask.
`detect_tempo` walks up from the wide-band fit while that ratio holds, pinned
with controls both ways: a synthetic 148 BPM click reads 148, a 74 BPM click
reads 74 and is rejected against a 148 ask.

## §2 Trending sound: both named sources need a credential

| source | state |
|---|---|
| TikTok Creative Center | endpoints that exist return HTTP 200 + `{"code":40101,"msg":"no permission"}`; the `/song/rank_list` path 404s. The public page is client-rendered — 21 KB of shell, no chart data — so HTML parsing yields nothing. Needs browser automation against the operator's session, or a TikTok for Business credential. |
| Spotify | `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` unset; registering the app is the operator's to do. `audio-features` (the BPM source) is also restricted for apps registered after 2024-11, so tempo availability must be checked rather than assumed — the code degrades to measuring tempo from the 30 s preview. |

Deezer was probed as an unauthenticated alternative and returned 0 results;
the hunt stopped there rather than continuing to a third source.

`status()` reports the reason per source, so an empty result is never
mistakable for "nothing is trending".

## §4 Reference-conditioned generation is not available today

Three catalogue apps accept an audio reference. All three fail, for three
different reasons, and **every probe was charged $0.00**:

| app | field | outcome |
|---|---|---|
| `minimax/music-cover` ($0.15/cover) | `reference_audio` | *"cover mode does not support instrumental music (no lyrics detected, dtw_result is empty)"* — it aligns against vocals |
| `infsh/diffrythm` (unpriced) | `reference_audio` | `Unknown language:` — requires LRC lyrics with a detectable language |
| `infsh/tencent-song-generation` (unpriced) | `prompt_audio` | `setup failed: No such file or directory: .../demucs/ckpt/...` — the app image is missing a checkpoint. Failed twice. |

The pattern: these are **song** generators — lyrics and vocals — and the reel
needs an instrumental bed. `elevenlabs/music`, the one that does instrumental
beds, takes no reference. So the §4 route to "trending sound without a claim"
is closed until one of those apps changes, independently of §2's credentials.

The melody-similarity check is built and calibrated anyway, so the threshold
is measured before it is ever needed: five unrelated pairs score 0.281–0.411,
a track against itself scores 1.000, and `MAX_MELODY_SIMILARITY` is 0.55.

## §5 not executed

Nothing subscribed. With §4 closed, a licensed library's similar-search is
currently the only route to a track *in* the trending sound rather than
merely in the genre.

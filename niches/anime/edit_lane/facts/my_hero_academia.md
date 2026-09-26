# My Hero Academia — fact ledger

Every id here is citable to a primary source. Nothing unsourced reaches screen.

| id | claim | source |
|---|---|---|
| F201 | The fight is Deku vs Overhaul | Crunchyroll's own title on the clip: "Deku vs Overhaul \| My Hero Academia" (`hm-zgEdCRes`) |
| F202 | Overhaul: "What I'm going to tear down is this world--its very framework!" | burned distributor subtitle, `hm-zgEdCRes` @ 12–16 s |
| F203 | Overhaul: "Your justice is small. You only see what's in front of you." | burned distributor subtitle @ 18–20 s |
| F204 | Overhaul: "It's mere sentimentalism." | burned distributor subtitle @ 21–23 s |
| F205 | Overhaul: "You hero pretenders..." | burned distributor subtitle @ 24–26 s |
| F206 | Overhaul: "Stay out of my way!" | burned distributor subtitle @ 27–29 s |
| F207 | Deku: "If I can't save... the one small girl in front of me... how can I become... a hero who saves everyone?" | burned distributor subtitles @ 39, 45, 54–57, 78–81 s — one sentence, delivered across the fight |
| F208 | Source audio is Japanese | faster-whisper multilingual language id on the clip's own audio: `ja`, p=0.74 |
| F209 | Rights notice carried in-frame | "© K. Horikoshi / Shueisha, My Hero Academia Project", burned bottom-right throughout |

## Deliberately NOT asserted

- The girl Deku refers to is not named on screen in this clip. I have not added
  a name tag for her, and no slam says one. Naming her would need a source
  outside the clip, and the fact gate does not accept "widely known".
- No claim about which season, episode or arc this is: the clip does not say,
  and Crunchyroll's title does not either.

## Added 2026-09-26 — the Shie Hassaikai arc names

| id | claim | source | verified by |
|---|---|---|---|
| F210 | This clip is from episode 76 | Crunchyroll's own description on `hm-zgEdCRes`: "Deku and Overhaul face off in episode 76!" | agent, fetched |
| F211 | Episode 76 is titled "Infinite 100%" | Crunchyroll's own canonical URL, reached by following their link from the clip description: `crunchyroll.com/my-hero-academia/episode-76-infinite-100-792407` | agent, fetched |
| F212 | Overhaul's organisation is the Shie Hassaikai | Prime Video S4 listing `primevideo.com/detail/0R9WXC5S1R89EBYVWP188IU3KY`; corroborated by the episode synopsis indexed for IMDb `tt11488600`: "As the fight with the Shie Hassaikai reaches its climax, Overhaul looks back on how it all started, and Izuku uses One for All at 100%." | **operator, attested** — the agent could not reach either page (Prime Video renders no episode list to a fetch; IMDb 403) |
| F213 | The girl in this fight is Eri | Prime Video S4 listing (above); Crunchyroll DVD listing, MediaMarkt Art.-Nr. 2732701 | **operator, attested** — MediaMarkt returns 403 to the agent |
| F214 | Overhaul's name is Kai Chisaki | Prime Video S4 listing (above) | **operator, attested** — not independently reachable |
| F215 | Eri is on screen in this clip at src 43.84–45.60 | the clip itself: a face detected at x 0.39–0.54, y 0.09–0.36 across 8 frames at detector score 0.73–0.84, cropped and looked at — a small child, white hair, a single horn on the forehead | agent, verified against the box |

**On F215 and the image model.** The model was asked who this face was with Eri
described in the prompt, horn included. It answered `mirio` ×3, `nighteye` ×3,
`unsure` ×1, and **never `eri`**. The crop sheet carried its `nighteye` label.
The operator caught it. This is rule
`framing.identity_is_verified_against_a_box_never_trusted_alone` doing the work
it was written for: the BOX was right every time, the NAME was wrong every time.

**Provenance matters more than the claim.** F212–F214 are recorded as
operator-attested, not agent-verified, because three of the four cited hosts
refuse an automated fetch. A later reader can see exactly who checked what.

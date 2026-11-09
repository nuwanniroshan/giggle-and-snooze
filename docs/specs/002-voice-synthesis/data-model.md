# Data Model: Voice Synthesis (spec 002)

## Output layout (`build/<book-slug>/audio/`, regenerable, never hand-edited)

| Path | Content |
|------|---------|
| `clips/<key>.wav` | One line's speech: mono, 24 kHz, 16-bit PCM, edges trimmed to at most 0.1 s, pitch-lifted for the kids |
| `clips/<key>.json` | `{line_id, speaker, voice, semitones, spoken, measure: {duration, peak, longest_silence, wps, cut_off}, attempts}` |
| `tracks/<track>.wav` | The intro, `page-PP` or outro clips stitched with pauses |
| `tracks/<track>.json` | Timing: `{track, track_key, duration, lines: [{line_id, speaker, role, text, start, end, clip, quality, attempts}]}` |
| `preview.wav` | Every track in order, with 1.0 s between tracks |
| `preview.json` | Preview sidecar `{preview_key, tracks: [[name, track_key], …]}`, written after `preview.wav` |
| `review.md` | Listening review sheet: one table per track (line, speaker, duration, peak, longest silence, words/s, attempts, original text) |
| `status.json` | `{status: "pending review" \| "approved", script_sha256, settings_sha256, synthesized_at, approved_at}` |

`<key>` is the first 32 hex characters of the sha256 of `{text: spoken, voice, semitones, repo, revision, temperature, top_p, max_tokens, sample_rate}`.

## Config

| File | Content |
|------|---------|
| `config/voices.json` | `model {repo, revision, support_repos[{repo, revision, files?}], temperature, top_p, max_tokens, seed}`, `supported_voices`, `pitch_semitones {role: semitones}`, `pauses {same_speaker, speaker_change, narration_to_qa, between_tracks}`, `checks {…, max_attempts}`, `sample_rate` |
| `config/cast.json` | Per-role `voice` (storyteller tara, kid_older zac, kid_younger zoe). It is copied into each episode script's `cast`, and synthesis reads the voice from the script |
| `book_data.json` `metadata.pronunciations` | Optional `{word: spoken respelling}`. Applied whole word and case-insensitively to spoken text only |

## Review fixes

- **`track_key`** is the first 32 hex characters of the sha256 of `{lines: [{key, audio_sha256, line_id, speaker, role, text, measure, attempts}], gaps, sample_rate}`, where `audio_sha256` hashes the clip WAV bytes. A re-synthesized clip (`--force`, deleted WAV) or a text/role edit that keeps the spoken identity rebuilds the track and its timing JSON.
- **`preview_key`** is the sha256 of `[[[track, track_key], …], pauses.between_tracks, sample_rate]`, stored in `preview.json`. The preview is rebuilt when the key differs or either file is missing.
- **Clip identity** leaves out `model.seed` on purpose: a seed change must not discard passing clips (it still resets the status through `settings_sha256`). Use `--force` to re-synthesize with a new seed.
- **Reused clips** are re-checked against the current `checks`; a stored measure that no longer passes is re-synthesized.
- **Distinct voices:** two cast roles sharing a voice is a `ConfigError` naming both roles and the voice (AC-2.4).
- **Approve** also refuses when `settings_sha256` (voices config, cast, pronunciations) changed since synthesis. `review.md` is written before `status.json`, which is the commit point.
- **Support repos:** mlx-audio loads `mlx-community/snac_24khz` and the bf16 tokenizer by name, so `download` writes the pinned revision to `<HF_HUB_CACHE>/models--<org>--<name>/refs/main`, and the engine refuses to load (`VoiceUnavailable` + download hint) unless `refs/main` equals the pin and the pinned snapshot is cached.

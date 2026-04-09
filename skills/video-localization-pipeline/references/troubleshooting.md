# Video Localization Pipeline Troubleshooting

Prefer inspecting workspace artifacts before changing commands.

## Read `manifest.json` first

Use `workspace/<video>/manifest.json` to confirm:

- which TTS provider actually ran
- whether clone mode was active
- whether `--line-sync`-style settings were active
- how many merged TTS segments were synthesized
- which downstream steps reran after a config change

Focus on:

- `steps.tts.provider`
- `steps.tts.metadata.voice_mode`
- `steps.tts.metadata.providers`
- `steps.tts.metadata.merged_segment_count`
- `steps.tts.metadata.config_snapshot`

## Symptom guide

If `en.srt` already reads awkwardly:

- focus on translation and segmentation first
- do not blame TTS until the text itself looks reasonable

If `en.srt` looks fine but spoken English crosses subtitle boundaries:

- inspect `merged_segment_count`
- if it is much smaller than the subtitle count, try `--line-sync`

If clone mode sounds wrong:

- inspect `clone_reference.wav`
- inspect `clone_reference.txt`
- verify `steps.tts.provider` is `voxcpm2`

If reruns do not seem to pick up new flags:

- inspect `steps.<name>.metadata.config_snapshot`
- confirm you are rerunning from the same repo root and workspace
- only clear artifacts manually if config snapshot reruns are clearly insufficient

## Fast timing workflow

When the issue is limited to a short section, avoid full rerenders.

1. Run a report-only alignment check on a suspicious chunk.
2. Compare that same chunk with `--line-sync`.
3. Re-synthesize only the short window if the report suggests a merge problem.
4. Apply `--line-sync` to the full pipeline only if the local preview actually helps.

## What `--line-sync` changes

This mode biases TTS toward one subtitle line per synthesis chunk by forcing more conservative merge behavior.

Expect:

- more TTS calls
- slower full-video runs
- tighter per-line timing
- potentially less fluid long-form phrasing

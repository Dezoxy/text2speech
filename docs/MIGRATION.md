# Migration Notes

## Runtime/CLI Migration

No breaking CLI migration is required.

### Root launcher

- `python3 launcher.py ...`

### Preferred

- `t2s ...`

## Internal Code Migration

Main runtime logic moved to package modules:

- `text2speech.pipeline_app`
- `text2speech.make_mp3_app`

Shared internals moved under `text2speech/shared/`.

When migrating internal imports, prefer:

- `text2speech.shared.tts_engine`
- `text2speech.shared.reporting`
- `text2speech.shared.manifest`
- `text2speech.shared.models`

instead of duplicating logic in app entry modules.

# README assets

PNGs embedded in `README.md` / `README.en.md` (relative paths).
PyPI uses the short `README.pypi.md`, which points at absolute
`raw.githubusercontent.com` URLs for the one demo screenshot it embeds.

## Architecture

| File | Shows |
|------|--------|
| `architecture-overview.jpg` | Four-layer overview: sources → ASL Daily Pipeline → lake → consumers (bilingual) |

## Terminal screenshots

| File | Shows |
|------|--------|
| `asl-demo.png` | `asl demo` phased progress + sample bars |
| `asl-query.png` | `asl query` SQL result with `source` |
| `asl-load.png` | Python `load()` REPL |

```bash
.venv/bin/python scripts/render_readme_screenshots.py
```

Banner copy should track `asl demo` (no mootdx). Sample bar numbers may be
from an older live run; re-render after UX copy changes.

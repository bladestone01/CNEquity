# README assets

PNGs embedded in `README.md` / `README.en.md` (relative paths).
PyPI uses the short `README.pypi.md`, which points at absolute
`raw.githubusercontent.com` URLs for the one demo screenshot it embeds.

## Architecture

| File | Shows |
|------|--------|
| `architecture-overview.png` | Four-layer overview: sources → ASL Daily Pipeline → lake → consumers (bilingual) |

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

## Dashboard screenshots

These are real captures, not rendered text, so they need a running server and a
lake with something in it.

| File | Shows |
|------|--------|
| `asl-serve.png` | Overview: KPI row, tier table, coverage heatmap |
| `asl-serve-dataset.png` | `trade_ticks` metadata tab — contract, schema, source horizon |

```bash
asl serve --config configs/ashare-lake.toml --port 8791
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=2 --window-size=1280,1530 \
  --virtual-time-budget=9000 --screenshot=docs/assets/asl-serve.png \
  "http://127.0.0.1:8791/"
```

`--force-device-scale-factor=2` is what makes the text readable when GitHub
scales the image down to ~860px. Pick `--window-size` height from
`document.body.scrollHeight` on the loaded page, or the capture cuts off
mid-heatmap. Wait for the "度量表过期" banner to clear (background stats
rebuild) before capturing — it is a transient state, not something the README
should advertise.

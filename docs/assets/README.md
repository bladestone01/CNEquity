# README assets

READMEs embed three raster/vector assets: survivorship chart, `asl-demo.png`,
and `asl-serve-hero.png`. Architecture is rendered from Mermaid in each README
so that it stays reviewable beside code changes.
Other PNGs below are for docs / social / re-exports.
PyPI uses the short `README.pypi.md`, which points at absolute
`raw.githubusercontent.com` URLs for the one demo screenshot it embeds.

## Social preview

Set in **Settings → Social preview**, not embedded in either README — GitHub
reads `og:image` from the repo setting, and a 1MB marketing card above the fold
would only push the actual content down.

| File | Size | Use |
|------|------|-----|
| `og-image-brand.png` | 1280×640 | The one to upload. GitHub caps the social preview at 1MB. |
| `og-image.png` | 1280×640 | Earlier variant without the brand panel. |
| `social-preview.png` | 1774×887 | Higher-res master of `og-image-brand.png`. Too large to upload as-is; keep it for re-exports. |
| `og-image.html` | — | Source the PNGs are rendered from. |

## Charts

| File | Shows |
|------|--------|
| `survivorship-gap.svg` | Survivorship bias, English labels — embedded in `README.en.md` |
| `survivorship-gap.zh.svg` | The same numbers with Chinese labels — embedded in `README.md` |

Same geometry and same measurement; only the string table differs. Re-render
both after a backfill changes the numbers:

```bash
python scripts/survivorship_gap.py --lang en --svg docs/assets/survivorship-gap.svg
python scripts/survivorship_gap.py --lang zh --svg docs/assets/survivorship-gap.zh.svg
```

## Architecture

`architecture-overview.png` is a legacy export and is no longer embedded. The
current diagram lives as Mermaid in `README.md` and `README.en.md`; update both
when storage layers, orchestration, quality, query, operations, Serve, or MCP
boundaries change.

## Terminal screenshots

| File | Shows |
|------|--------|
| `asl-demo.png` | `asl demo` phased progress + sample bars — embedded in both READMEs |
| `asl-query.png` | `asl query` SQL result with `source` (kept for re-exports) |
| `asl-load.png` | Python `load()` REPL (kept for re-exports) |

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
| `asl-serve-hero.png` | 1440×820 current overview: health, 42 datasets, KPIs, coverage heatmap and action state — in both READMEs |
| `asl-serve.png` | 1440px-wide full-page overview (source / docs) |
| `asl-serve-dataset.png` | `trade_ticks` metadata tab (for docs; not in README) |

```bash
asl stats rebuild
asl serve --config configs/ashare-lake.toml --port 8791
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1440,820 --virtual-time-budget=9000 \
  --screenshot=docs/assets/asl-serve-hero.png \
  "http://127.0.0.1:8791/"
```

Capture `asl-serve.png` separately as a full-page screenshot at the same
1440px viewport width. Before saving either image, confirm the page shows
“运行正常”; “度量表过期” is a transient stats state and should be cleared with
`asl stats rebuild` rather than advertised in the README.

# README screenshots

Terminal-style PNGs embedded in `README.md` / `README.en.md`:

| File | Shows |
|------|--------|
| `asl-demo.png` | `asl demo` phased progress + sample bars |
| `asl-query.png` | `asl query` SQL result with `source` |
| `asl-load.png` | Python `load()` REPL |

Regenerate (needs Pillow in the venv):

```bash
.venv/bin/python scripts/render_readme_screenshots.py
```

Numbers come from a short live `asl demo` run; re-render after UX copy changes.

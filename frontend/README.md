# frontend

Source for the `asl serve` dashboard bundle.

```bash
cd frontend && npm ci && npm run build
```

Output is `src/ashare_lake/serve/static/bundle.js`, and it is **committed**.
That is the trade this directory exists to make:

- `pip install ashare-lake` needs no node. The bundle ships in the wheel.
- Only a contributor changing the dashboard needs npm, and only to re-run
  `npm run build` before committing.
- CI runs `npm run check`, which rebuilds and fails if the committed bundle does
  not match the source — so the two cannot drift.

ECharts is imported through `echarts/core` with explicit chart and component
registrations rather than the prebuilt `echarts.min.js`: tree-shaking takes it
from 1.1MB to ~595KB (205KB gzipped), and the unused half is every chart type
this dashboard does not draw.

No CDN, ever. A lake often runs on an offline box or behind a proxy where an
external asset is a page that does not load.

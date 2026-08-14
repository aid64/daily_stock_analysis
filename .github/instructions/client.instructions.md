---
applyTo: "daily_stock_analysis/apps/dsa-web/**,daily_stock_analysis/apps/dsa-desktop/**,daily_stock_analysis/scripts/run-desktop.ps1,daily_stock_analysis/scripts/build-desktop*.ps1,daily_stock_analysis/scripts/build-*.sh,daily_stock_analysis/docs/desktop-package.md"
---

# Client Instructions

- Preserve the existing Vite + React web structure and Electron desktop runtime assumptions; reuse current API/state patterns instead of adding parallel client abstractions.
- If a change affects API fields, auth state, route behavior, Markdown/chart rendering, local backend startup, or report payloads, assess both Web and Desktop compatibility.
- Validate Web changes with `cd daily_stock_analysis/apps/dsa-web && npm ci && npm run lint && npm run build` when feasible.
- Validate Desktop changes by building Web first, then `daily_stock_analysis/apps/dsa-desktop`; if platform limits prevent full Electron validation, call out the exact risk in the final delivery.

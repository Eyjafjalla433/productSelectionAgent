# Shopping Copilot Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan inline.

**Goal:** Populate option 3 with actual documented fields in an interactive local preview.

**Architecture:** Keep the existing backend intact, use its demo HTTP server through a Vite proxy, and derive all product and decision state from responses. Separate normalization, API transport, state orchestration and visual components.

**Tech Stack:** React, TypeScript, Vite, Phosphor icons, Inter, Vitest.

**Spec:** ../specs/2026-09-26-toc-design.md

## Global Constraints

- English UI; no inferred currency, pictures, purchase links or stock.
- Preserve option 3 proportions and saved-first rail.
- No mutation retries; serialize session operations.
- Same-origin /api proxy; backend demo data clearly labeled.
- Original backend files are read-only for this change.

## Review Focus

- Null prices/ratings must not become zero or claim a budget match.
- Structured follow-up options must work even when question is null.
- Selection responses replace state and invalidate stale comparison/finalization.
- Product detail is a root object, with alternate field names.
- Narrow viewports and long product titles must not hide composer or actions.

### Task 1: Data boundary

Files: src/contracts.ts, src/model.ts, src/api.ts, tests/model.test.ts, tests/api.test.ts.
Interfaces: ProductCard / ProductDetail / SelectionState / ChatResponse / Handoff; formatPrice, formatRating, requirementGroups, replyOptions, matrixRows, ApiClient.post<T>.
- [ ] Write tests covering nulls, zero, strings, replies and basic matrix fallback; run red.
- [ ] Implement types and adapters; run green.
- [ ] Test errors and no retry/serialized requests; implement centralized client; run tests.

### Task 2: Selected visual and core flow

Files: src/App.tsx, src/useShopping.ts, src/components.tsx, src/Overlays.tsx, src/styles.css, index.html, vite.config.mjs.
Consumes Task 1 contracts; produces interactive three-column screen and accessible overlays.
- [ ] Implement API state, demo-only seeded preview, new conversation, chat, details, save/remove, compare and finalize.
- [ ] Populate product fields, matching evidence, nullable prices and ratings, requirements and saved-first rail.
- [ ] Add responsive tabs, keyboard composer, dialog focus and visible pending/error/empty/success states.
- [ ] Run unit suite and production build; inspect main flow through actual demo backend.

### Task 3: QA and delivery

Files: README.md, field-mapping.md, design-qa.md, qa/ screenshots.
- [ ] Open preview in in-app browser; capture desktop and mobile.
- [ ] Compare selected image and implementation together; fix actionable drift.
- [ ] Exercise all core actions and check browser errors; request fresh code review while completing QA.
- [ ] Complete final tests/build, record limits and leave preview open.

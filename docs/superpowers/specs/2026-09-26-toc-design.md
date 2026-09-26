# Shopping Copilot — selected option 3

The user selected the third displayed concept and authorized filling its fields. Build a local interactive design preview using the existing API and its synthetic demo catalog. Keep the original backend untouched. This is not a public production launch.

## Visual and interaction design

- Source: `design-reference/selected-option-3.png`, 1487 × 1058. Recreate the three-column proportions (26.4% / 47.2% / 26.4%), pale blue side panels, white product list, Inter typography, cobalt controls, and light dividers. Replace placeholder lines with API content as requested.
- Desktop: left conversation and fixed composer, center ranked recommendations, right saved candidates above requirements. Independent scroll regions keep controls available.
- Mobile: Chat / Products / Compare navigation; requirements below the saved section; full-screen detail and comparison surfaces.
- English UI throughout, including errors, accessible names and empty states. No product pictures, purchasing, stock, or fabricated values.
- Seed a fresh demo-only session with a blue dress search and two saved candidates to match the selected visual. New conversation clears this sample. Only seed after health explicitly identifies demo data.
- Show products sorted by rank, text evidence and two-column recommendation reasoning. Details open in a drawer. Saved options are keyed by parent_asin. The compare dialog uses dynamic matrix dimensions and per-cell evidence where supplied; fall back to basic comparison rows. Finalize saves the decision only.
- Show missing values honestly. Numeric price retains supplied number without inferred currency. Missing evidence is unknown, not a product defect. No score/confidence badges.
- UI count, undo and redo reflect backend state. Compare requires 2 selected items; finalize requires 1. Read max_selections. Candidate/requirement mutations invalidate comparison; backend finalized state is authoritative.
- API operations serial, one response applied atomically; no automatic mutation retry or streamed text simulation. Preserve typed input on failure. Session expiry offers new conversation and clears old data.

## Structure and acceptance

React + TypeScript + Vite; Phosphor icons; self-hosted Inter. Same-origin /api proxy to local backend port 8000. Components for chat, product rows, saved/preferences rail, detail drawer and comparison dialog. Pure adapters for the three product shapes and nullable fields, structured replies and dynamic comparisons. Central API client and state hook.

Validate data adapters, error handling and serialization with focused tests, then build. In the in-app browser verify desktop and mobile render, save/remove/limit, details, comparison, finalization, invalidation, new conversation, chat and keyboard/focus behavior. Record visual QA against the selected target. Real search indexes and model integration remain unverified; synthetic catalog is labeled.

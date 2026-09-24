# Show Me Your Agent

A conversational shopping assistant that helps people compare products without forcing them through a fixed questionnaire.

## What it does

- Extracts multiple requirements from a single message and remembers them.
- Asks a targeted follow-up only when product evidence makes it useful.
- Shows up to ten candidates with a comparison of the top three.
- Answers questions about a displayed product without restarting the search.
- Supports changes of mind, undo, redo, and retry after a search failure.
- Saves a shortlist with product evidence and an auditable execution record.

## Run the demo

From the repository root:

```sh
python -B -m agentic_workflow
```

This uses the existing `search_tool` integration. Its internal files are not modified.

Generate a standalone, English-language replay using synthetic products:

```sh
python -B -m agentic_workflow.showcase --backend demo
```

Open `agentic_workflow/showcase_output/index.html`. The replay includes four executed turns and a downloadable conversation and shortlist. Prices and ratings in this mode are simulated.

To regenerate the replay using the actual search model and product index:

```sh
python -B -m agentic_workflow.showcase --backend search_tool
```

## Example conversation

> I want to buy a black tshirt around $30.
>
> What material is the second one made of?
>
> Is it loose fit?
>
> Actually, change the color to blue.
>
> Undo.
>
> Compare #1 and #2.
>
> Finalize my selection.

## Limits and transparency

### Changing your mind

Use **Undo** or **Redo** for search requirements. Use **Undo selection** or **Redo selection** for shortlist edits. Shortlist history also covers changes made with the interface buttons, and restored items remain a draft rather than automatically becoming finalized. Changing requirements or rejecting a product clears the previous shortlist-edit history.

**Don't finalize yet** keeps the choices as a draft. **Do not clear my shortlist** leaves the selection untouched. The system distinguishes these from genuine product feedback such as **I don't like #2**.

Undo and redo buttons load a command into the composer for review; they do not send it automatically or overwrite an unfinished message.

Recommendations are ranked by search relevance, not sales. The actual search dataset does not provide live prices, ratings, or inventory. Missing facts are not invented, and shortlist confirmation does not place an order. The audit chain detects changes to exported records; it is not a signature or proof of identity.

The interface, assistant replies, scripted demos, and generated showcase use English. Input parsing still recognizes supported Chinese phrases. Raw user messages and source evidence remain unchanged in audit records rather than being silently translated.

## Verification

```sh
python -B -m agentic_workflow.verify
python -B -m agentic_workflow.mvp.ui_smoke
```

The browser smoke test requires the configured local browser debugging endpoint. It uses mocked API responses; it is not a live-search integration test.

For the real search backend:

```sh
python -B -m agentic_workflow.live_smoke --conversation
```

The live scenario now covers 24 turns: initial search, material and price questions, short follow-ups, an unsupported product question, a tentative color change, pause, confirmation, undo, uncertainty, redo, comparison, shortlist confirmation, removal, shortlist undo/redo, deferred confirmation, a negated clear command, and reconfirmation. It checks that detail questions and shortlist controls retain product order and requirements without retrieving again. Assistant replies and shopping-guide content are also checked for Chinese text.

Latest verification: 214 regression tests, 22 browser checks, and the 24-turn real-backend scenario passed. All 19 files inside `search_tool` retained their original hashes. These checks establish the covered behavior, not unrestricted natural-language understanding or live price/inventory availability.

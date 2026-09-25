# Show Me Your Agent

A conversational shopping assistant that helps people compare products without forcing them through a fixed questionnaire.

## What it does

- Extracts multiple requirements from a single message and remembers them.
- Understands common singular and plural product names when the shopper starts or switches a search, including “dress” and “dresses.”
- If a shopper considers or requests different product types (“a dress or a jacket,” “deciding between a dress and a jacket,” or “a dress and a jacket”), the MVP keeps any stated budget and asks which type to explore first instead of silently choosing one. “Show me both” explains the current one-category-at-a-time search boundary and keeps the choices available. A reply such as “Dress, preferably blue” selects a type and saves the added preference together.
- For a compound request such as “a black T-shirt and blue jeans under $60,” the product-specific colors remain attached to their respective categories while the shared price cap is retained. Choosing one category applies only its own color; a newer explicit color in the reply takes precedence.
- Deferred item details remain available after the first category is explored: “now the jeans” restores their color. The shopper can also say “actually make the jeans green” before choosing a category or while browsing a dress; the agent updates only the remembered jeans, keeps the current results, and offers the category choices again. “I would prefer green jeans, but blue is fine” saves a soft first choice rather than switching away from an open dress page or imposing a green-only filter; that priority survives later category switches. “Any color is fine for the jeans” leaves their color open without abandoning the dress page. “Any color except red for the jeans” also clears the old color requirement but retains red as a jeans-only exclusion, not a dress rule; “I need jeans, any color except red” works as a direct request too. A mixed edit such as “no color preference for the jeans, but size L” is one undoable change. “Make the jeans green and keep both under $50” saves the jeans edit and a shared cap in one turn; a visible dress page is rechecked against it. By contrast, “make the jeans green and under $50” saves a jeans-only cap, leaving a dress search at its original limit. Switching between items restores each effective price rule, and Undo remains available. Unpriced items are not presented as verified budget matches. Category-switch Undo/Redo and Start over restore or clear the deferred details with the search state; an unrelated new product target drops them.
- “Jeans” is a narrower, catalog-verified subtype within the pants category: title or taxonomy evidence is required, so a description that merely mentions jeans does not turn chinos into jeans. “Show me pants instead” removes the subtype filter; Undo restores it.
- Keeps a stated gift recipient, occasion, and budget while asking for a searchable product type; later corrections can be undone.
- Asks a targeted follow-up only when product evidence makes it useful.
- The optional-question planner no longer relies on a fixed 20-candidate cutoff. It estimates how many retrieved options a grounded answer could remove and raises the required benefit after prior optional questions. A sharply divided 18-item pool may warrant a question; a manageable 12-item pool does not. Results remain visible either way, and explicit browsing still skips the optional question.
- A shopper can redirect a question toward fabric, fit, or color, but changing topics does not restart an endless optional-question loop. After the attention budget is spent, the agent keeps the current results in place; a direct preference remains usable and undoable.
- "I prefer cotton, but polyester is okay" and "I prefer regular fit, but loose fit is okay" keep a weighted first choice and a visible fallback. The recap states that order, and Undo reverses it. A listing mentioning both alternatives earns only its strongest match within that attribute, not two bonuses.
- When no product passes the catalog checks, names the current product search, offers a relevant requirement to revisit or a category switch, and does not silently loosen filters. A shopper's correction and its Undo remain separate, reversible actions.
- When it asks a catalog-grounded follow-up about unpriced results, the chat reply also says that an approximate budget cannot be verified; the price limitation is not confined to a sidebar note.
- Phrases candidate-grounded questions in shopper language, naming listed choices while making it easy to keep all options open or see results first.
- If a shopper asks what a supported fit or fabric term means, the MVP gives a short, general explanation and a sizing or composition caveat—even before a search. It does not treat the question as a preference or rerun search. If a choice was open, its answer options remain available afterward. This is a limited apparel glossary, not unrestricted product education.
- When the shopper asks “Why are you asking about color?” or “Why did you ask that?”, the assistant explains the current question from its actual scope and catalog evidence without searching again, saving a preference, or closing the choice. Optional color, fabric, and fit questions are identified as optional; a missing product type and an unpriced strict budget get different, specific explanations.
- The explanation can share a turn with an answer or correction: “Why are you asking about color? I prefer black” explains the question and saves black as one undoable preference edit. “Why are you asking about price? Show unpriced ideas” resolves the budget choice, while “Why are you asking about color? Start over” respects the reset instead of silently saving color.
- A shopper can combine a supported terminology question with a new requirement in one turn: "What does regular fit mean? I prefer loose fit" explains the first phrase and saves only loose fit as an undoable preference. A courtesy-only ending such as "Thanks" remains read-only.
- Two read-only questions can also share a turn: "What does regular fit mean? Is #2 pure cotton?" explains the fit label and checks #2's listed composition without changing the search. Asking for #2's price in the same pattern reports an unlisted price honestly. The open refinement choice remains answerable.
- A combined explanation and control request also respects the control: "What does regular fit mean? Start over" resets the search, while saved products trigger a reset-scope choice before anything is cleared. "Reset everything" can be undone to restore the earlier search and shortlist; "Cancel" leaves them in place.
- If the shopper answers an optional question with a different useful detail, keeps that detail and shows results instead of immediately asking another optional question; a category switch starts a new question scope.
- Natural replies such as "I'm open to either," "Either color works for me," and "No strong preference" close an optional choice without inventing a filter or treating the answer as an error. "I don't mind either, but cotton please" also keeps the new fabric requirement and allows Undo. The same flexible wording can decline an optional two-product comparison without rerunning search.
- A mid-question correction such as "Actually, not a T-shirt, a blue dress" treats the T-shirt as rejected context, not the new search target. It shows dress results and Undo restores the prior T-shirt page. Explicit strict budgets remain strict when the external catalog has no prices.
- “Either is fine,” “any of those is fine,” or “all are okay” ends an optional facet question without inventing a preference, repeats, or a corrective-sounding error. The current results stay visible, and an accompanying new requirement is still applied. When the question is which product category to explore first, the agent keeps that choice available instead of pretending both categories have been searched.
- Explicit exploration such as “I am just browsing T-shirts” or “show me T-shirts for ideas” starts with a visible result batch and no optional narrowing question. If the shopper later names a focus, the agent asks about it only when the retrieved listings support a useful distinction; otherwise it explains the evidence limit. An ordinary “I need a T-shirt” can still receive an evidence-backed optional question alongside results.
- A shopper can change pace without restating requirements: after browsing, “help me choose” keeps the same visible page and evaluates one useful, catalog-grounded question. If the current pool does not justify one, it offers to compare the displayed options instead. “Actually, I just want to browse” then dismisses the optional question, keeps the same page and requirements, and removes the pressure to narrow. Browsing pace persists through a small preference edit: “Cotton would be nice” is saved as a soft, undoable preference without reopening an optional question; “Help me choose” explicitly opts back into guidance. These pace changes do not trigger novelty paging or invent a purchase commitment.
- If the shopper names what matters—such as “fabric matters more”—uses that as a question focus, not a literal material requirement; asks about supported choices or explains when the catalog cannot distinguish them.
- Repeating the same focus does not trigger another identical optional question or an unhelpful new search. The current results stay visible, a pending supported choice remains answerable, and an evidence-poor catalog is described honestly.
- An explicit "show me the results now" wins over an optional focus question. "Show me first" keeps the displayed list and does not search again; "show me more" requests a new batch. Skipping an optional question never relaxes a strict price limit.
- "Show me more" keeps paging through unseen verified products. When the available search results are exhausted, it labels the repeated list honestly instead of presenting old items as new discoveries.
- An exhausted "show me more" also leaves the shopper's last visible page in place, rather than jumping back to the first page. That retention is skipped when a product was hidden or a rejection was undone, so an invalid old list is never restored.
- If the shopper then changes a requirement and uses Undo, the agent restores the page they were actually comparing—not the fallback batch the search backend returned internally. Redo and another Undo preserve that same view.
- Shows up to ten candidates with a comparison of the top three.
- The top-three comparison calls out differences in explicitly listed sizes and cuts when the catalog supports them. Ambiguous multi-size or alternative-fit titles do not become definite claims; size availability and personal fit still need checking.
- Answers questions about a displayed product without restarting the search.
- Answers multiple product questions in one turn, then applies any separately stated preference change. References such as “#2” stay tied to the list visible before that change.
- Explains a displayed item's match using catalog evidence while distinguishing relevance rank from verified quality.
- Handles a price-comparison request without inventing missing prices or replacing the current list.
- Distinguishes catalog average ratings from review volume, and says when neither is available.
- Understands relative fit refinements such as "something looser" as undoable preferences; catalog-supported fit matches can move ahead without becoming hard filters.
- Preserves the distinction between "I prefer black, but white is okay too" and "black or white is fine": a verified first-choice color can lead the search results, while the acceptable alternative stays visible. Equal alternatives retain search relevance order; either preference is undoable.
- A shopper can reverse that first-choice color, or make both colors equally acceptable, in one message. Undo and Redo restore the preference order as well as the displayed result order.
- Treats “not too baggy” as reversible, soft fit guidance: an explicitly baggy listing can move lower without being excluded, while an unlabeled fit remains unverified.
- Keeps “I think size M” tentative while preserving firm details in the same message; explicit size evidence can lift a listing without filtering other sizes, and a later correction can be undone.
- Understands “I wear L, not M” as a size correction and “size M or L” as two allowed sizes; a negated size is not silently saved as a positive requirement.
- Supports changes of mind, undo, redo, and retry after a search failure.
- Treats "none of these work for me" as feedback about the displayed batch, not as a request for workwear. It favors unseen results, acknowledges the feedback, and says plainly when this search has no fresh verified option; an actual "for work" requirement in the same message is still retained.
- Answers natural questions such as "What do you remember about me?" and "What do you know about me?" with the current session's requirements, saved products, and hidden products; it does not claim to expose an account-wide profile. This recap is read-only, does not rerun search, and keeps an open refinement question answerable.
- Can keep a product from the visible list while refining the search in the same turn; unsupported shortlist/search combinations ask for an explicit order instead of silently dropping half the request.
- Applies separate shortlist instructions to their own ranks: “keep #2 and remove #3” is one auditable edit, not a command to remove both products.
- Can exclude a displayed product and request more results in one turn. The new retrieval sees the exclusion, and the reply says if that search fails.
- For “more like #1,” asks which verified facet matters only when needed. The shopper can choose fit, color, or verified pure-cotton fabric, and add another detail in the same reply.
- “Undo rejection” and “Redo rejection” reverse the last product exclusion by saved product ID, even after ranks change. Neither command silently changes search requirements or refreshes the visible list.
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
> Is #2 pure cotton? How much does #3 cost? Also change to blue.
>
> I like #2, but show me blue ones.
>
> Keep #2 and remove #3.
>
> I don't like #2. Show me more.
>
> Undo rejection.
>
> Show me more like #1.
>
> Fit and blue.
>
> Why is #1 first?
>
> Do you have anything cheaper?
>
> Which one has the best reviews?
>
> Something looser, please.
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

## Interaction benchmark

[Google's conversational shopping update](https://blog.google/products-and-platforms/products/search/search-ai-updates-september-2025/) describes starting with an ordinary-language request and refining results through follow-up preferences. [Amazon's shopping-assistant update](https://www.aboutamazon.com/news/retail/amazon-rufus-ai-assistant-personalized-shopping-features) emphasizes remembering preferences and comparing products, while its [About You controls](https://www.aboutamazon.com/news/retail/amazon-about-you-personalization-preferences) acknowledge that preferences change. Our MVP applies those interaction principles with reversible session-level edits, optional catalog-grounded questions, and a top-three comparison. A correction can interrupt an optional question without losing the old search to Undo. We do not claim Amazon-like cross-device memory; this dataset also cannot verify live price or inventory, and the assistant says so.

## Limits and transparency

### Changing your mind

Use **Undo** or **Redo** for search requirements. Use **Undo selection** or **Redo selection** for shortlist edits. Use **Undo rejection** or **Redo rejection** for the last product exclusion. Shortlist history also covers changes made with the interface buttons, and restored items remain a draft rather than automatically becoming finalized. Changing requirements or rejecting a product clears the previous shortlist-edit history. Reversing a rejection makes the product eligible for a future search; it does not insert it into the current list.

**Don't finalize yet** keeps the choices as a draft. **Do not clear my shortlist** leaves the selection untouched. The system distinguishes these from genuine product feedback such as **I don't like #2**.

Undo and redo buttons load a command into the composer for review; they do not send it automatically or overwrite an unfinished message.

Switching product categories starts a fresh search without carrying over the old category's shortlist or hidden-product exclusions. One **Undo** restores the prior category, displayed results, shortlist, and hidden-product choices; **Redo** returns to the newer category. This is a session-level change of mind, not a purchase action.

“Let's start over” clears the current session's search requirements and displayed results in one reversible edit when there are no saved or hidden products. It does not launch another search or assume a new product type. Undo restores the earlier requirements and displayed list; Redo can clear them again. The interface offers a Start over shortcut during an active search. If products are saved or hidden, the assistant asks whether to reset the search only, reset everything, or cancel. “Reset search only” clears filters and results while keeping the shortlist and product exclusions. “Reset everything” explicitly clears all three; one Undo restores the previous search, shortlist, and hidden-product exclusions, and Redo clears them again. The agent never silently deletes a saved or hidden product. This reset covers only the current session, not account-level shopping memory.

Recommendations use search relevance and disclosed preference evidence, not sales. When a soft fit preference has catalog support, matching items may move ahead while original search order is preserved within each group. The actual search dataset does not provide live prices, ratings, or inventory. Missing facts are not invented, and shortlist confirmation does not place an order. The audit chain detects changes to exported records; it is not a signature or proof of identity.

The category taxonomy maps “jeans” to “pants,” then applies an additional jeans subtype check against catalog title or taxonomy evidence. This is lexical evidence, not proof of fabric composition, size availability, or fit; inspect the listing before buying.

If a catalog lists prices, the assistant can identify cheaper displayed items and labels those amounts as catalog prices, not live offers. With the actual `search_tool`, prices are absent: it says the comparison cannot be verified and retains the current results.

A hard price limit cannot be verified against the actual unpriced search index. If otherwise matching products are found, the assistant offers two explicit choices: keep the strict limit and show no unverified matches, or preview unpriced ideas. Choosing the preview moves an upper cap into an approximate target and a lower floor into a separate unverified floor preference; Undo restores the hard bounds. “Over $30 but under $50” retains both numbers, while a separately stated target such as “around $30 and under $50” remains $30 in preview. A newer hard bound removes an earlier soft price target only when they conflict. The agent never labels these ideas as within the unverified range.

For review comparisons, the assistant reports average rating and rating count separately. It does not claim to have read review text or checked live scores. The actual `search_tool` contains no ratings or review counts, so the assistant keeps the list and states that limit.

For subjective comfort questions, the assistant keeps the current results and preferences unchanged. It does not treat “Which one is most comfortable?” as a new search requirement or infer real wearing comfort from a seller's title. It offers a narrower, optional follow-up only when some visible listings explicitly mention a supported detail such as breathability or relaxed fit. A one-phrase answer is applied as a reversible preference; the shopper can also provide that answer and a budget together. A decline such as “No thanks” leaves the products and preferences untouched; “No thanks, but under $30” applies only the budget. A different request cancels the offered choice. When the listings provide no useful distinction, it does not offer an empty follow-up. An explicit item question or unavailable rank receives a direct answer. This is deliberately narrower than review-informed product Q&A such as [Amazon's shopping assistant](https://www.aboutamazon.com/news/retail/amazon-rufus), because this index has no comparable wear-test or review-text evidence.

When asked which displayed item is better for the shopper, the assistant compares the two named items (or the first two if none are named) using only listed attributes and supported session preferences. It treats the ranking as search relevance, not proof of quality or personal fit. If the evidence does not distinguish the items, it says so, keeps the results and shortlist unchanged, and does not run another search. Shared facts appear once; absent prices are not repeated as a caveat under every item. If both listings verify different fits or colors, it offers one optional preference question instead of asserting a winner. A short answer such as “the first one” is tied to that verified difference only on the next turn. The shopper can add another detail in the same reply—“the first one, but under $30”—and undo both changes together. “Neither” or “no preference” closes that optional question without a new search or a hidden preference.

The interface, assistant replies, scripted demos, and generated showcase use English. Input parsing still recognizes supported Chinese phrases. Raw user messages and source evidence remain unchanged in audit records rather than being silently translated.

For a direct buying-decision question such as "Which one should I buy?", the assistant stays on the current page and compares the first three displayed items. It names verified differences that could change the choice, then asks which matters more when no item has a defensible advantage. A short answer such as "fabric" maps to the just-offered catalog detail, saves an undoable preference, and can be combined with another constraint such as "under $30"; one-click choices also allow the shopper to leave both options open. The preference answer does not mean "show me more" or hide the original candidates. The agent then gives a catalog-supported starting pick if one is available, reports when several displayed items support the same detail, and makes no pick if none verify it. A later correction such as "Actually, fit matters more than fabric" keeps fabric as a lower-weight secondary preference and focuses on fit; Undo restores the prior weight. The explicit priority remains stronger than a newer secondary preference after intervening turns, while the original source turn for the fit value remains intact. If fabric was a hard requirement, the assistant explicitly says it kept that must-have and offers the supported phrasing "100% cotton is optional" to relax it; the requirement is never silently removed. Missing listing details are treated as unknown, not as product drawbacks. This is a limited, catalog-only adaptation of [Amazon's Help Me Decide](https://www.aboutamazon.com/news/retail/amazon-things-to-buy-help-me-decide-gen-ai) pattern and [Alexa for Shopping's conversational corrections](https://www.aboutamazon.com/news/retail/amazon-rufus-ai-assistant-personalized-shopping-features); it does not use purchase history, live offers, or review evidence.

The unchanged external search tool supplies candidate scores. The adapter now uses catalog-supported, weighted soft color, fit, and fabric preferences when they distinguish candidates, retaining the tool's order within equal-evidence groups. A controlled pair swaps order when the shopper changes fit-versus-fabric priority; a listing saying "not cotton" receives no cotton boost or supported-material badge and fails a hard cotton requirement. Candidate-grounded fabric questions use that same evidence check, so a "cotton-free" listing is not counted as a cotton option. "Why is #1 first?" explains the supported preference and effective weighting when those factors moved an item, rather than attributing the position solely to generic relevance. A real-catalog replay kept its top three in the same order, so the controlled swap is not a claim that every priority edit visibly reorders a page.

The soft “not too baggy” preference is inspired by [Google Shopping’s conversational “barrel jeans that aren’t too baggy” example](https://blog.google/products-and-platforms/products/search/search-ai-updates-september-2025/). This MVP handles only a narrow degree phrase with explicit catalog fit evidence; it does not infer how an unlabelled item wears or claim to understand arbitrary fit descriptions.

Uncertainty is scoped to the detail it qualifies: “black is a must, but I think size M” keeps black required and size M optional. Soft size does not become a search term or hard filter. Listings with an explicit matching size may rank ahead, but unlabeled sizing remains unverified; the assistant advises checking the seller's size chart. This is a limited implementation of the conversational refinement described by [Google Shopping](https://blog.google/products-and-platforms/products/search/search-ai-updates-september-2025/) and natural-language preference corrections described for [Alexa for Shopping](https://www.aboutamazon.com/news/retail/amazon-rufus-ai-assistant-personalized-shopping-features).

Natural size corrections such as “Actually I wear L, not M” replace the earlier size and remain undoable; “size M or XL” retains both in the stated order. The parser covers these explicit patterns, not all sizing idioms or brand-specific size conversions. This is a narrower implementation of the conversational preference-correction behavior described by [Alexa for Shopping](https://www.aboutamazon.com/news/retail/amazon-rufus-ai-assistant-personalized-shopping-features).

Optional narrowing is interruptible. If the assistant asks about color and the shopper instead says “maybe size M,” it records the tentative size, leaves color open, and displays results without immediately switching to a material question. A hard requirement that leaves no verified matches can still trigger a recovery question. Switching product category resets the question scope so relevant questions about the new product can be asked. This follows the natural follow-up direction shown in [Google AI Mode](https://blog.google/products-and-platforms/products/search/search-ai-updates-september-2025/), but uses bounded catalog evidence rather than open-ended visual understanding.

When an evidence-scored follow-up is warranted, the wording refers to listed colors, materials, styles, or use cases rather than exposing internal facet names. It explicitly offers to keep options open or “show me first.” The choice still comes from the same candidate coverage and expected-reduction checks; this is a presentation improvement, not a claim that the catalog proves personal fit. The gentler dialogue direction is informed by the [Kate Spade AI Gift Concierge description](https://www.aboutamazon.com/news/aws/aws-agentic-shopping-assistant-retailers).

The shopper can also steer the next question without naming a product attribute value. “Fabric matters more to me” prioritizes a catalog-grounded material question; it is not stored as the material “fabric.” If the category is not yet known, that focus survives the category question, and “actually style matters more” can redirect it. When the returned listings do not support a useful split, the assistant keeps the results and says it cannot make that comparison. This draws on the attribute-led shopping guidance described in [Amazon AI Shopping Guides](https://www.aboutamazon.com/news/retail/amazon-ai-shopping-guides-product-research-recommendations), with an explicit evidence threshold and no claim of universal understanding.

“More like #1” is a bounded, catalog-grounded refinement, not visual or open-ended semantic similarity. It does not assume which aspect of #1 matters. It offers only unambiguous listed fit, variant color, or verified pure-cotton composition not already captured in the shopper's requirements. Shoppers can answer with natural phrases such as “I like the black color” or select all offered details at once. If they say “both” after three details were offered, the agent asks which two without losing the reference item. If they dislike a verified fit or color, it becomes a reversible exclusion and the results refresh; an ambiguous fabric dislike does not become a blanket ban on all cotton. “No longer avoid slim fit” removes only that exclusion; it does not silently make slim fit a positive preference. A repeated ambiguous reply exits the question rather than looping. A mixed request such as “more like #1 but in blue” applies blue and searches immediately, without mistaking “like #1” for a shortlist command; any other verified detail of the old #1 remains an optional follow-up. Each preference change can be undone separately. If all supported details are already known, it browses for more results under the existing preferences instead of repeating the question. It reports when no new verified option is available. A fresh search may still include the reference item if no distinct eligible alternative can be verified.

The shopper can give opposite examples in one turn: “Hide #2 and show me more like #1” hides #2 from later retrieval while keeping the current page and asking which verified trait of #1 matters. The same works in reverse clause order and with “I don't like #2.” The two ranks are resolved against the displayed list before either action commits; an invalid or self-contradictory reference changes nothing. Undo rejection remains separate from a later undoable similarity preference. If the shopper undoes or redoes the rejection before answering the similarity question, the visible question and its choices remain available; the two kinds of change stay independent.

A side question about a displayed product also keeps an unanswered similarity choice open. For example, after “Show me more like #1,” asking “How much does #2 cost?” answers from the catalog without rerunning search or losing the original #1 reference. The shopper can still choose fit, color, or fabric afterward. An explicit new shopping request remains free to replace that choice.

“Show me more like #1 but cheaper” keeps both parts of the request tied to #1. When that displayed item has a catalog price, the assistant searches below it using a strict cent-level cap, labels the comparison as catalog—not live—pricing, and asks which separately verified fit, color, or fabric detail should carry over. The price edit is undoable. When the reference has no catalog price, it keeps the list unchanged, explains that cheaper cannot be verified, and asks for an explicit price limit without inventing one. An invalid rank cannot become a shortlist selection. A follow-up can also add one or more explicit details in the same message, such as “cheaper and blue and regular fit.” Those details are saved together and remain undoable even if the reference item is unpriced; the assistant does not pretend that the cheaper part was verified. A second shortlist command in that sentence is not silently executed. If the same sentence also says “under $45,” the agent uses the stricter of the reference-derived ceiling and the shopper's explicit cap. When the reference is unpriced, only the shopper's own cap is saved; unpriced listings remain unverified and are not shown as budget matches. The combined edit is reversible with one Undo.

“What are my preferences?” gives a read-only recap of the current session's required details, softer preferences, and exclusions. It does not claim cross-session personal memory. An open question about a referenced product remains available after the recap, and the recap does not consume the shopper's undo history or trigger a new search.

Named corrections such as “Remove the black requirement” and “Forget the cotton preference” remove that positive value; they do not re-add it or turn it into a negative exclusion. If a slot allowed black or blue, removing black keeps blue. “Remove black, but keep cotton” is handled as one undoable turn, leaving unrelated requirements intact.

Corrections inside a single message also replace the earlier value: “a black T-shirt, actually blue” searches for blue, while “a black or blue T-shirt” retains two allowed colors. The same rule covers “blue instead of black,” product type, material, price cap, and gift recipient. Other details supplied in that message remain intact. This is deterministic coverage of these phrasing patterns, not a claim to understand every correction idiom.

When a later turn revises several details, the reply names each applied change and a few unchanged details from the actual state. For example, after “Actually blue, size L, around $40,” it confirms the color, size, and approximate-budget revisions while retaining cotton; one Undo restores the previous state. It still acknowledges saved corrections if the new combination has no verified product, without presenting any item as a match. The acknowledgement is derived from the state diff rather than from the shopper's wording alone, so a removed requirement is not described as retained. This is session-only correction, not a claim of cross-session personal memory.

Budget withdrawal is separate from removing a product: “Remove my $30 budget” clears price limits, approximate targets, and any unverified floor preference, while “remove the price cap” clears only the upper limit. Dollar amounts in these sentences are not mistaken for displayed product ranks. Both changes can be undone without erasing color or material requirements.

The shopper can also relax a named dimension or value in ordinary language. “I don't care about fit anymore” removes the saved fit preference without changing color. “I don't care about color anymore, but keep cotton” clears color and retains cotton. “I don't care about black anymore” removes only black from an allowed black-or-blue choice; it does not exclude black or erase blue. These changes can be undone. A comment about the fit or color of a specific displayed item is not treated as a blanket withdrawal.

The catalog-driven clarification question also accepts “neither” or “no preference” without saving those words as a product attribute or asking the same question again. The current results remain visible. A reply such as “neither, but cotton” applies cotton while declining the offered choice, and can be undone.

A bounded question does not blindly save any short reply as its target attribute. Recognized off-list answers still work, but an unrelated word leaves preferences and results unchanged; the agent explains that it could not use the reply and exits the question instead of looping.

If the product category is still missing, “whatever” is not stored as a category. The agent offers a few selectable starting points and waits without searching; those choices remain available after an uncertain reply. The shopper can also supply several details at once, such as “black cotton tshirt,” and they are all captured together. These are category suggestions, not cross-category product recommendations.

For a broad request such as “a gift for my dad under $50,” the assistant keeps the gift purpose and price cap but does not invent a product category or search the catalog prematurely. It offers concrete starting points. “A black T-shirt for my mom instead” can then set the product type and color while correcting the recipient in one turn. “A birthday gift for my sister around $40” also retains the occasion; “actually it’s for her graduation” changes that occasion without losing the recipient or product details, and Undo restores birthday. “A T-shirt for my sister’s birthday” works without the word “gift”; a later correction to “my brother’s graduation” changes both details together and can be undone. A dress for someone’s wedding does not imply that the dress is a gift. Gift purpose and occasion survive product-type switches, but are conversation context—not product-search terms, ranking signals, or proof that a listing suits that person. This follows the recipient-and-occasion dialogue pattern described for [Kate Spade’s gift concierge](https://www.aboutamazon.com/news/aws/aws-agentic-shopping-assistant-retailers), within a much narrower catalog. Open-ended cross-category gift discovery is not yet supported; when the shopper says “surprise me,” the assistant explains that it needs a starting product type instead of fabricating recommendations.

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

The live scenario covers 39 turns: initial search, grounded rank and review questions, material and price questions, an unpriced cheaper-items request, short follow-ups, a relative fit refinement and undo, mixed preference-and-question turns, a keep-and-refine turn with separate shortlist and requirement undo, a two-action shortlist edit and undo, an unsupported product question, a tentative color change, pause, confirmation, undo, uncertainty, redo, comparison, shortlist confirmation, removal, shortlist undo/redo, deferred confirmation, a negated clear command, and reconfirmation. A separate four-turn real-backend check confirms that rejecting #2 and requesting more results excludes that item before retrieval, then undoing and redoing the rejection leaves the displayed list unchanged. Another five-turn real-backend check binds “more like #1” to its catalog record, asks for a supported facet, searches from the shopper's answer, restores the prior results on undo, and retains the #1 reference for a follow-up question. Assistant replies and shopping-guide content are also checked for Chinese text.

Latest verification: 500 regression tests, 31 browser checks, a real-backend blue-jeans replay returning ten subtype-verified candidates with a valid audit, a deferred-item correction replay where selecting green jeans returned two hard-matching candidates with a valid audit, a real-backend shared-budget edit that rechecked the active dress results and withheld unpriced matches with a valid audit, a four-turn real-backend item-only budget replay that restored the shared dress cap after switching back with a valid audit, a real-backend deferred soft-color replay that retained green-first and blue-acceptable preference across category switches with a valid audit, a real-backend deferred color-withdrawal replay that retained the dress page and returned ten unconstrained-color jeans with a valid audit, a real-backend jeans-only red-exclusion replay that preserved the dress page and returned ten jeans without a displayed red-title conflict, with a valid audit, a real-backend broad T-shirt replay in which “Either is fine” kept the same results and closed the optional color question with a valid audit, a real-backend browse-first replay that showed ten T-shirts without an optional question and honestly reported when a later fabric focus could not be distinguished, with a valid audit, an evidence-based question-utility replay on the real 46-candidate T-shirt pool with a valid audit, a real-backend browse-to-decision replay that kept the same ten-item page while asking a grounded color question, with a valid audit, a real-backend return-to-browsing replay that dismissed that question without changing the page and passed audit, a two-turn real-backend compound-product replay that retained the black T-shirt intent and returned ten verified products, a three-turn real-backend comfort-question and decline replay, a five-turn real-backend single- and multi-detail correction acknowledgement and Undo replay, a twelve-turn real-backend start-over, scoped/full-reset, Undo/Redo, shortlist, and hidden-product replay, synthetic comfort-choice, combined-budget, decline-with-budget, interruption, and Undo checks, an English four-turn synthetic replay, the 39-turn real-backend scenario (including natural candidate-question wording), six real-backend unpriced-budget turns covering both a cap and a range with Undo, a seven-turn real-backend gift-occasion correction covering both explicit and possessive phrasings, a three-turn real-backend soft-fit guidance and Undo replay, a three-turn real-backend tentative-size correction using “I wear L, not M” and Undo, a three-turn optional-question bypass and Undo replay, a real-backend shopper-focus replay with an honest catalog-evidence fallback, and separate feedback, similarity, preference-withdrawal, plain-language indifference, read-only comparison, optional-question-decline, unmatched-reply, unsure-category, and single-message-revision replays passed. The unsure-category replay also selects a suggested product type against the real index. Synthetic catalog tests cover reversible preferences, bounded ambiguity recovery, gift-context corrections, and comparisons without invented personal-fit claims. The product-facing runtime has no fixed turn cap; a separate test confirms refinement and undo beyond ten turns. All 19 files inside `search_tool` retained their original hashes. Direct material checks answer yes or no only when complete, consistent composition evidence supports the answer; otherwise they state the uncertainty. These checks establish the covered behavior, not unrestricted natural-language understanding or live price/inventory availability.

A two-turn replay against the actual unpriced `search_tool` also confirmed that “Show me more like #1 but cheaper” kept the same ten-item page, did not add a price cap, explained the missing catalog price, and passed the conversation audit.
The mixed “Show me more like #1 but cheaper and blue” replay against that backend applied blue, preserved #1 as the reference, left price uncapped, explained that #1 has no catalog price, returned ten results, and passed audit.
With an explicit $25 cap added to that request, a second real-backend replay saved blue and the cap together, returned no unverified products, explained the missing reference price, and passed audit.
The combined-request chat reply now names the reference item's listed price, the active limit, and the result count in shopper language. If the catalog has no prices, it gives one caveat and keeps the budget question active rather than repeating the warning or hiding the available choices.
A two-turn real-backend question-transparency replay asked “Why did you ask that?” after a broad T-shirt request. The assistant explained the catalog-supported black, gray, and white choice, kept the ten-item page and open color question, and passed audit without searching again.
A second two-turn real-backend replay asked “Why are you asking about color? I prefer black.” The assistant explained the question, saved black as a soft preference, returned ten products, and passed audit.
A three-turn real-backend replay hid #2 while asking for more like #1, kept the first page visible, asked which listed trait of #1 mattered, then returned ten follow-up products without #2. The full sequence passed audit.
A four-turn real-backend replay of that mixed request plus Undo rejection and Redo rejection confirmed that the hide state changed in the expected direction while the similarity question stayed visible and the audit remained valid.

A separate four-turn real-backend replay confirmed that an unpriced question about #2 kept the ten-item page and #1 similarity reference intact. The shopper then answered “color,” triggering the intended refinement, and the audit passed.

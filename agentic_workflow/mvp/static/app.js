const ui = {
  chat: document.querySelector("#chat"),
  replyOptions: document.querySelector("#reply-options"),
  products: document.querySelector("#products"),
  resultsHead: document.querySelector("#results-head"),
  resultSummary: document.querySelector("#result-summary"),
  shortlist: document.querySelector("#shortlist"),
  shortlistItems: document.querySelector("#shortlist-items"),
  shortlistCount: document.querySelector("#shortlist-count"),
  shortlistStatus: document.querySelector("#shortlist-status"),
  clearShortlist: document.querySelector("#clear-shortlist"),
  undoShortlist: document.querySelector("#undo-shortlist"),
  redoShortlist: document.querySelector("#redo-shortlist"),
  finalizeSelection: document.querySelector("#finalize-selection"),
  exportSelection: document.querySelector("#export-selection"),
  comparison: document.querySelector("#comparison"),
  comparisonTable: document.querySelector("#comparison-table"),
  comparisonDetails: document.querySelector("#comparison-details"),
  comparisonNote: document.querySelector("#comparison-note"),
  storyGuide: document.querySelector("#story-guide"),
  storyProgress: document.querySelector("#story-progress"),
  storyName: document.querySelector("#story-name"),
  storyPrompt: document.querySelector("#story-prompt"),
  storyNext: document.querySelector("#story-next"),
  exportAudit: document.querySelector("#export-audit"),
  composer: document.querySelector("#composer"),
  message: document.querySelector("#message"),
  submit: document.querySelector("#composer button"),
  status: document.querySelector("#status"),
  runtimeMode: document.querySelector("#runtime-mode"),
  dataBoundary: document.querySelector("#data-boundary"),
  scenarios: document.querySelector("#scenarios"),
  stateEmpty: document.querySelector("#state-empty"),
  stateGroups: document.querySelector("#state-groups"),
  stateChanges: document.querySelector("#state-changes"),
  intentVersion: document.querySelector("#intent-version"),
  action: document.querySelector("#decision-action"),
  reason: document.querySelector("#decision-reason"),
  candidates: document.querySelector("#candidate-count"),
  shown: document.querySelector("#shown-count"),
  novelty: document.querySelector("#novelty-count"),
  questions: document.querySelector("#question-count"),
  latency: document.querySelector("#latency"),
  support: document.querySelector("#support-count"),
  tokens: document.querySelector("#token-count"),
  noveltyNote: document.querySelector("#novelty-note"),
  turnLabel: document.querySelector("#turn-label"),
  turnProgress: document.querySelector("#turn-progress"),
  pipeline: document.querySelector("#pipeline"),
  shadowBoard: document.querySelector("#shadow-board"),
  modelAssist: document.querySelector("#model-assist"),
  modelAssistName: document.querySelector("#model-assist-name"),
  modelAssistDetail: document.querySelector("#model-assist-detail"),
  turnAudit: document.querySelector("#turn-audit"),
  newSession: document.querySelector("#new-session"),
};

let sessionId = null;
let sessionUsable = false;
let currentTurn = 0;
let maxTurns = null;
let scenarios = [];
const shortlisted = new Map();
let currentSelectionState = { status: "draft", finalized: false };
let activeScenario = null;
let scenarioStep = 0;

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.code = payload.error_code || "request_failed";
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setStatus(text, mode = "live") {
  ui.status.textContent = text;
  ui.status.className = `status ${mode}`;
}

function addMessage(role, text, attribute) {
  const article = document.createElement("article");
  article.className = `message ${role === "user" ? "user-message" : "agent-message"}`;
  const speaker = document.createElement("span");
  speaker.className = "speaker";
  speaker.textContent = role === "user" ? `YOU / TURN ${currentTurn + 1}` : `AGENT / TURN ${currentTurn}`;
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  article.append(speaker, paragraph);
  if (attribute) {
    const tag = document.createElement("span");
    tag.className = "ask-tag";
    tag.textContent = `ASKING: ${attribute.toUpperCase()}`;
    article.append(tag);
  }
  ui.chat.append(article);
  ui.chat.scrollTop = ui.chat.scrollHeight;
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return Object.values(value).join(", ");
  return String(value);
}

function renderReplyOptions(receipt = {}) {
  ui.replyOptions.replaceChildren();
  const question = receipt.question;
  const choices = [];
  if (question?.correction) {
    choices.push("Replace", "Keep both", "Keep original");
  } else if (Array.isArray(question?.options)) {
    question.options.slice(0, 4).forEach((value) => {
      if (typeof value === "string") choices.push(
        question.target_slot === "category" ? (question.option_labels?.[value] || value) : value);
    });
  } else if (Array.isArray(receipt.suggested_replies)) {
    receipt.suggested_replies.slice(0, 4).forEach((value) => {
      if (typeof value === "string") choices.push(value);
    });
  }
  if (question && !["category", "reset_scope"].includes(question.target_slot)) choices.push("Show me first");
  if (receipt.can_undo_requirements) choices.push("Undo");
  if (receipt.can_redo_requirements) choices.push("Redo");
  if (receipt.can_undo_rejection) choices.push("Undo rejection");
  if (receipt.can_redo_rejection) choices.push("Redo rejection");
  if ([receipt.hard, receipt.soft, receipt.excluded].some(values =>
    values && Object.keys(values).length)) choices.push("Start over");
  [...new Set(choices)].forEach((text) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => {
      if (!sessionUsable || ui.submit.disabled || ui.message.value.trim()) return;
      ui.message.value = text;
      ui.composer.requestSubmit();
    });
    ui.replyOptions.append(button);
  });
  ui.replyOptions.hidden = choices.length === 0;
  syncReplyOptions();
}

function syncReplyOptions() {
  const draft = Boolean(ui.message.value.trim());
  ui.replyOptions.querySelectorAll("button").forEach((button) => {
    button.disabled = !sessionUsable || ui.submit.disabled || draft;
    button.title = draft ? "Send or clear your draft first" : "Click to send, or type your own reply";
  });
}

function renderState(receipt) {
  const groups = [
    ["Hard constraints", receipt.hard, "", "hard"],
    ["Soft preferences", receipt.soft, "soft", "soft"],
    ["Excluded", receipt.excluded, "excluded", "excluded"],
  ].filter(([, values]) => values && Object.keys(values).length);
  ui.stateEmpty.hidden = groups.length > 0;
  ui.stateGroups.hidden = groups.length === 0;
  ui.stateGroups.replaceChildren();
  groups.forEach(([label, values, className, evidenceGroup]) => {
    const group = document.createElement("div");
    group.className = "state-group";
    const title = document.createElement("b");
    title.textContent = label;
    const chips = document.createElement("div");
    chips.className = "chips";
    Object.entries(values).forEach(([key, value]) => {
      const chip = document.createElement("span");
      chip.className = `chip ${className}`;
      const content = document.createElement("span");
      content.textContent = `${key}: ${formatValue(value)}`;
      chip.append(content);
      const source = receipt.state_evidence?.[evidenceGroup]?.[key];
      if (source?.evidence) {
        const evidence = document.createElement("small");
        evidence.textContent = `T${source.source_turn ?? "?"} · “${source.evidence}”`;
        evidence.title = source.evidence;
        chip.append(evidence);
      }
      chips.append(chip);
    });
    group.append(title, chips);
    ui.stateGroups.append(group);
  });
  ui.intentVersion.textContent = `INTENT V${receipt.intent_version || 1}`;
  ui.stateChanges.replaceChildren();
  const changes = receipt.state_changes || [];
  if (!changes.length) {
    const note = document.createElement("p");
    note.textContent = "No state changes this turn.";
    ui.stateChanges.append(note);
  } else {
    const list = document.createElement("div");
    list.className = "change-list";
    changes.forEach((change) => {
      const item = document.createElement("span");
      item.className = `change ${change.kind}`;
      const transition = change.kind === "updated"
        ? `: ${formatValue(change.previous)} → ${formatValue(change.value)}`
        : change.kind === "added"
          ? `: ${formatValue(change.value)}`
          : `: ${formatValue(change.previous)}`;
      item.textContent = `${change.kind} · ${change.slot}${transition}`;
      list.append(item);
    });
    ui.stateChanges.append(list);
  }
  if (receipt.intent_reset) {
    const reset = document.createElement("p");
    reset.textContent = "Intent changed · novelty memory started fresh.";
    ui.stateChanges.append(reset);
  }
  if (receipt.selection_finalization_invalidated) {
    const reopened = document.createElement("p");
    reopened.textContent = "Requirements changed · final selection reopened for review.";
    ui.stateChanges.append(reopened);
  }
}

function renderReceipt(receipt) {
  const action = receipt.post_action || receipt.pre_action || "observe";
  const reason = receipt.post_reason || receipt.pre_reason || "state_updated";
  ui.action.textContent = action.toUpperCase();
  ui.reason.textContent = reason.replaceAll("_", " ");
  ui.candidates.textContent = receipt.candidate_count || "—";
  ui.shown.textContent = receipt.shown_count;
  ui.novelty.textContent = `${receipt.new_product_count ?? "—"} / ${receipt.repeat_count ?? "—"}`;
  ui.questions.textContent = receipt.questions_asked;
  const lastTiming = receipt.timings.at(-1);
  ui.latency.textContent = lastTiming ? `${Math.round(lastTiming.elapsed_ms)} ms` : "—";
  const quality = receipt.result_quality;
  ui.support.textContent = quality ? `${quality.fully_supported_count} / ${quality.product_count}` : "—";
  const usage = receipt.model_usage || {};
  ui.tokens.textContent = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
  const noveltyCopy = {
    fresh_pool: "Previously shown products were excluded from this intent's fresh pool.",
    fallback_reuse: "Fresh pool exhausted; shown products were safely reused.",
    not_applied: "No prior shown products affected this result.",
  };
  ui.noveltyNote.textContent = noveltyCopy[receipt.novelty_mode] || "Novelty policy has not run yet.";

  const stageNames = ["1_intent", "3_state", "4A_pre_policy", "2_retrieval", "4B_ranking"];
  const timingMap = new Map(receipt.timings.map((item) => [item.stage.replace("_retry", ""), item]));
  [...ui.pipeline.children].forEach((item, index) => {
    const timing = timingMap.get(stageNames[index]);
    item.classList.toggle("done", Boolean(timing));
    item.querySelector("b").textContent = timing ? `${Math.round(timing.elapsed_ms)} ms` : index > 2 && action === "clarify" ? "skipped" : "queued";
  });
  renderShadowBoard(receipt.shadow_questions || []);
  renderModelAssist(receipt.model_assist || receipt.comparison_assist);
}

function renderModelAssist(assist) {
  ui.modelAssist.hidden = !assist;
  if (!assist) return;
  const accepted = assist.updates
    ? assist.updates.length
    : (assist.products || []).reduce((count, product) => count + (product.pros || []).length + (product.cons || []).length, 0);
  const usage = assist.usage_this_call || assist.usage || {};
  const tokens = (usage.prompt_tokens || 0) + (usage.completion_tokens || 0);
  ui.modelAssistName.textContent = `${String(assist.provider || "provider").toUpperCase()} / ${assist.model || "unknown model"}`;
  const unit = assist.products ? "comparison point(s)" : "requirement update(s)";
  const cache = assist.cached ? " · cached" : "";
  ui.modelAssistDetail.textContent = assist.warning
    ? `${accepted} grounded ${unit} · ${tokens} tokens${cache} · fallback: ${assist.warning}`
    : `${accepted} grounded ${unit} · ${tokens} tokens${cache} · ${Math.round(assist.latency_ms || 0)} ms`;
}

function renderShadowBoard(questions) {
  ui.shadowBoard.replaceChildren();
  if (!questions.length) {
    const note = document.createElement("p");
    note.textContent = "No candidate-grounded question yet. This board runs only after retrieval.";
    ui.shadowBoard.append(note);
    return;
  }
  questions.forEach((question) => {
    const card = document.createElement("article");
    card.className = "shadow-question";
    const header = document.createElement("header");
    const name = document.createElement("span");
    const value = document.createElement("b");
    name.textContent = question.attribute.toUpperCase();
    value.textContent = question.would_ask ? `+${question.net_value.toFixed(2)}` : question.net_value.toFixed(2);
    header.append(name, value);
    const options = document.createElement("div");
    options.className = "shadow-options";
    question.options.forEach((option) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${option.value} (${option.count})`;
      button.title = `Load clue: ${option.prompt}`;
      button.addEventListener("click", () => {
        ui.message.value = option.prompt;
        ui.message.focus();
      });
      options.append(button);
    });
    const meter = document.createElement("div");
    meter.className = "shadow-meter";
    const fill = document.createElement("i");
    fill.style.width = `${Math.max(0, Math.min(100, question.coverage * 100))}%`;
    meter.append(fill);
    card.append(header, options, meter);
    ui.shadowBoard.append(card);
  });
}

function appendTurnAudit(data) {
  if (data.turn === 1) ui.turnAudit.replaceChildren();
  const receipt = data.receipt;
  const action = receipt.post_action || receipt.pre_action || "observe";
  const entry = document.createElement("li");
  const title = document.createElement("span");
  const detail = document.createElement("p");
  title.textContent = `T${String(data.turn).padStart(2, "0")} · ${action.toUpperCase()} · INTENT V${receipt.intent_version}`;
  detail.textContent = `${receipt.candidate_count} candidates · ${data.products.length} shown · +${receipt.new_product_count} new · ${receipt.state_changes.length} state changes`;
  entry.append(title, detail);
  ui.turnAudit.prepend(entry);
}

function productColor(text) {
  const palette = ["#ff4f38", "#3757ff", "#167a61", "#a94fc5", "#d17b19"];
  const sum = [...text].reduce((value, char) => value + char.charCodeAt(0), 0);
  return palette[sum % palette.length];
}

function renderProducts(products, retained = false, guide = null) {
  ui.products.replaceChildren();
  if (products.length) {
    const panel = document.createElement('section');
    panel.className = 'shopping-top-three';
    const heading = document.createElement('h3');
    heading.textContent = products.length >= 3 ? 'Compare the top three' : 'Take a closer look';
    const note = document.createElement('p');
    note.textContent = 'Ranked by search relevance, not sales.' + (products.some(p => p.price == null) ? ' Prices are unavailable; budget fit still needs checking.' : '') + (guide?.comparison_takeaway ? ` ${guide.comparison_takeaway}` : '');
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    ['Rank / Product', 'Standout features', 'Other details'].forEach(label => {
      const cell = document.createElement('th'); cell.textContent = label; head.append(cell);
    });
    const body = table.createTBody();
    products.slice(0,3).forEach(product => {
      const notes = product.shopper_notes || {};
      const row = body.insertRow();
      [`#${product.rank} ${product.title}`, notes.feature || '—', notes.detail || '—'].forEach(value => {
        row.insertCell().textContent = value;
      });
    });
    panel.append(heading, note, table); ui.products.append(panel);
  }
  ui.resultsHead.hidden = products.length === 0;
  ui.resultSummary.textContent = products.length
    ? (retained === "restored_previous_results" ? `Restored ${products.length} previous options`
      : retained ? `Keeping ${products.length} previous options` : `${products.length} recommendations · Up to 10`)
    : "";
  products.forEach((product, index) => {
    const card = document.querySelector("#product-template").content.firstElementChild.cloneNode(true);
    card.style.setProperty("--product-color", productColor(product.category));
    card.style.animationDelay = `${Math.min(index, 6) * 45}ms`;
    card.querySelector(".product-rank").textContent = `#${String(product.rank).padStart(2, "0")}`;
    card.querySelector(".product-glyph").textContent = product.category.slice(0, 2).toUpperCase();
    card.querySelector(".product-category").textContent = product.category;
    card.querySelector("h3").textContent = product.title;
    card.querySelector(".product-store").textContent = `${product.store} · ${product.parent_asin}`;
    if (product.shopper_notes) {
      const shopperNote = document.createElement('p');
      shopperNote.className = 'shopper-summary';
      shopperNote.textContent = [product.shopper_notes.feature, product.shopper_notes.detail].filter(Boolean).join(' · ');
      card.querySelector('h3').after(shopperNote);
    }
    const price = product.price == null ? "PRICE N/A" : `$${Number(product.price).toFixed(2)}`;
    const rating = product.rating == null ? "NO RATING" : `★ ${product.rating} (${product.rating_count || 0})`;
    card.querySelector(".product-meta").replaceChildren();
    const priceNode = document.createElement("b");
    const ratingNode = document.createElement("span");
    priceNode.textContent = price;
    ratingNode.textContent = rating;
    card.querySelector(".product-meta").append(priceNode, ratingNode);
    const match = product.match || { hard_supported: 0, hard_total: 0, soft_supported: 0, soft_total: 0, signals: [] };
    const hardMatch = card.querySelector(".hard-match");
    hardMatch.textContent = match.hard_total ? `HARD ${match.hard_supported}/${match.hard_total}` : "NO HARD FILTERS";
    hardMatch.classList.toggle("complete", match.hard_supported === match.hard_total);
    card.querySelector(".soft-match").textContent = `SOFT ${match.soft_supported}/${match.soft_total}`;
    const advice = product.advice || { pros: [], cons: [] };
    [[".product-pros", advice.pros], [".product-cons", advice.cons]].forEach(([selector, rows]) => {
      const list = card.querySelector(selector);
      (rows || []).forEach((row) => {
        const item = document.createElement("li");
        item.textContent = row.text;
        item.title = `${row.source}: ${row.evidence}`;
        list.append(item);
      });
    });
    const signals = card.querySelector(".product-signals");
    match.signals.forEach((signal) => {
      const item = document.createElement("li");
      item.className = signal.status;
      item.textContent = `${signal.tier.toUpperCase()} · ${signal.slot}: ${formatValue(signal.value)} — ${signal.evidence}`;
      signals.append(item);
    });
    card.querySelector(".product-evidence").textContent = product.evidence.length ? `Rank signals: ${product.evidence.join(" · ")}` : "Rank signals: catalog evidence only.";
    card.querySelector(".match-disclaimer").textContent = match.disclaimer || "Lexical catalog evidence, not verified availability.";
    const shortlistButton = card.querySelector(".shortlist-button");
    const selected = shortlisted.has(product.parent_asin);
    shortlistButton.classList.toggle("selected", selected);
    shortlistButton.setAttribute("aria-pressed", String(selected));
    shortlistButton.textContent = selected ? "✓ SHORTLISTED" : "+ SHORTLIST";
    shortlistButton.disabled = !selected && shortlisted.size >= 3;
    shortlistButton.addEventListener("click", async () => {
      if (!sessionUsable || !sessionId) return;
      const requestSession = sessionId;
      const selected = !shortlisted.has(product.parent_asin);
      shortlistButton.disabled = true;
      try {
        const state = await api("/api/select", { session_id: requestSession, parent_asin: product.parent_asin, selected });
        if (sessionId !== requestSession) return;
        syncSelection(state, products);
        renderComparison(null);
        renderProducts(products, retained, guide);
      } catch (error) {
        if (sessionId !== requestSession) return;
        addMessage("agent", `Selection update failed: ${error.message}`);
        shortlistButton.disabled = false;
      }
    });
    const rejectButton = card.querySelector(".reject-button");
    rejectButton.addEventListener("click", () => {
      ui.message.value = `I don't like #${product.rank}.`;
      ui.message.focus();
    });
    const catalogDetail = card.querySelector(".catalog-detail");
    catalogDetail.addEventListener("toggle", async () => {
      if (!catalogDetail.open || catalogDetail.dataset.loaded === "true") return;
      const body = catalogDetail.querySelector(".catalog-detail-body");
      body.textContent = "Loading catalog evidence…";
      try {
        const detail = await api("/api/product", {
          session_id: sessionId,
          parent_asin: product.parent_asin,
        });
        body.replaceChildren();
        (detail.product_description || []).forEach((value) => {
          const paragraph = document.createElement("p");
          paragraph.textContent = value;
          body.append(paragraph);
        });
        const bullets = detail.product_bullet_points || [];
        if (bullets.length) {
          const list = document.createElement("ul");
          bullets.forEach((value) => {
            const item = document.createElement("li");
            item.textContent = value;
            list.append(item);
          });
          body.append(list);
        }
        const facts = document.createElement("dl");
        Object.entries(detail.details || {}).forEach(([key, value]) => {
          const term = document.createElement("dt");
          const definition = document.createElement("dd");
          term.textContent = key;
          definition.textContent = value;
          facts.append(term, definition);
        });
        if (facts.children.length) body.append(facts);
        const note = document.createElement("small");
        note.textContent = detail.source_note;
        body.append(note);
        catalogDetail.dataset.loaded = "true";
      } catch (error) {
        body.textContent = `Could not load details: ${error.message}`;
      }
    });
    ui.products.append(card);
  });
}

function renderShortlist() {
  ui.shortlist.hidden = shortlisted.size === 0 && !currentSelectionState.can_undo_selection && !currentSelectionState.can_redo_selection;
  ui.undoShortlist.hidden = !currentSelectionState.can_undo_selection;
  ui.redoShortlist.hidden = !currentSelectionState.can_redo_selection;
  ui.redoShortlist.title = "Load a redo command. Your search requirements will not change.";
  ui.undoShortlist.title = "Load an undo command. Your search requirements will not change.";
  ui.shortlistCount.textContent = `${shortlisted.size} / 3`;
  const finalized = Boolean(currentSelectionState.finalized && shortlisted.size);
  ui.shortlist.classList.toggle("finalized", finalized);
  ui.shortlistStatus.textContent = finalized ? "✓ FINALIZED" : "DRAFT";
  ui.exportSelection.disabled = shortlisted.size === 0;
  ui.finalizeSelection.disabled = shortlisted.size === 0 || finalized;
  ui.finalizeSelection.textContent = finalized ? "✓ FINALIZED" : "FINALIZE";
  ui.shortlistItems.replaceChildren();
  shortlisted.forEach((product) => {
    const item = document.createElement("article");
    item.className = "shortlist-item";
    const title = document.createElement("b");
    const meta = document.createElement("span");
    title.textContent = product.title;
    meta.textContent = `${product.price == null ? "PRICE N/A" : `$${Number(product.price).toFixed(2)}`} · ${product.rating == null ? "NO RATING" : `★ ${product.rating}`}`;
    item.append(title, meta);
    const needsReview = product.match?.signals?.some(signal =>
      (signal.tier === "hard" && signal.status !== "supported") ||
      (signal.tier === "excluded" && signal.status === "conflict"));
    if (needsReview) {
      const review = document.createElement("p");
      review.className = "shortlist-caution";
      review.textContent = "Saved earlier. Not all current requirements are supported by its listed details.";
      item.append(review);
    }
    const draft = product.comparisonDraft;
    if (draft) {
      const label = document.createElement("small");
      label.className = "comparison-draft-label";
      label.textContent = "From the listed details";
      item.append(label);
    }
    const pro = draft?.pros?.[0] || product.advice?.pros?.[0];
    const con = draft?.cons?.[0] || product.advice?.cons?.[0];
    if (pro) {
      const fit = document.createElement("p");
      fit.className = "shortlist-fit";
      fit.textContent = `+ ${pro.text}`;
      fit.title = `${pro.source}: ${pro.evidence}`;
      item.append(fit);
    }
    if (con) {
      const caution = document.createElement("p");
      caution.className = "shortlist-caution";
      caution.textContent = `− ${con.text}`;
      caution.title = `${con.source}: ${con.evidence}`;
      item.append(caution);
    }
    ui.shortlistItems.append(item);
  });
}

function syncSelection(state, products) {
  if (!state) return;
  currentSelectionState = state;
  const desired = new Set(state.selected_asins || []);
  [...shortlisted.keys()].forEach((asin) => {
    if (!desired.has(asin)) shortlisted.delete(asin);
    else delete shortlisted.get(asin).comparisonDraft;
  });
  (state.selected_products || []).forEach((product) => {
    if (desired.has(product.parent_asin)) {
      shortlisted.set(product.parent_asin, {...shortlisted.get(product.parent_asin), ...product});
    }
  });
  (products || []).forEach((product) => {
    if (desired.has(product.parent_asin)) shortlisted.set(product.parent_asin, product);
  });
  // Preserve the backend's shortlist order, including restored selections.
  const ordered = [...desired].map(asin => [asin, shortlisted.get(asin)]).filter(([, product]) => product);
  shortlisted.clear();
  ordered.forEach(([asin, product]) => shortlisted.set(asin, product));
  renderShortlist();
}

function applyComparisonAssist(handoff) {
  const rows = handoff?.comparison_assist?.products || [];
  rows.forEach((row) => {
    const product = shortlisted.get(row.parent_asin);
    if (product && (row.pros?.length || row.cons?.length)) product.comparisonDraft = row;
  });
  if (rows.length) renderShortlist();
}

function renderDescriptionComparison(handoff) {
  const result = handoff?.comparison_assist;
  if (result?.schema_version !== 'description-comparison.v1') return false;
  const products = handoff.selected_products || [];
  ui.comparison.hidden = products.length === 0;
  if (!products.length) return true;
  const titleById = new Map(products.map(product => [product.parent_asin, product.title]));
  const label = value => String(value).replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());
  const heading = (parent, text) => {
    const element = document.createElement('h3'); element.textContent = text; parent.append(element);
  };
  const sourceDetails = (parent, refs) => {
    if (!refs?.length) return;
    const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = 'View source'; details.append(summary);
    refs.forEach(ref => {
      const quote = document.createElement('blockquote');
      quote.textContent = ref.quote; details.append(quote);
    });
    parent.append(details);
  };
  const matrix = result.objective_comparison?.comparison_matrix || [];
  const tableFor = rows => {
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    ['Listed detail', ...products.map(p => p.title)].forEach(text => {
      const th = document.createElement('th'); th.scope = 'col'; th.textContent = text; head.append(th);
    });
    const body = table.createTBody();
    rows.forEach(row => {
      const tr = body.insertRow();
      const th = document.createElement('th'); th.scope = 'row'; th.textContent = label(row.dimension); tr.append(th);
      products.forEach(product => {
        const cell = tr.insertCell();
        const attribute = row.values?.[product.parent_asin];
        const value = attribute?.value;
        cell.textContent = value == null ? 'Unknown' : typeof value === 'object' ? JSON.stringify(value) : String(value);
        if (attribute?.source_type === 'inferred') {
          const badge = document.createElement('small'); badge.className = 'inference-label'; badge.textContent = 'Inference'; cell.append(badge);
        }
        if (attribute?.evidence) sourceDetails(cell, [{quote: attribute.evidence}]);
      });
    });
    return table;
  };
  const known = matrix.filter(row => products.some(p => row.values?.[p.parent_asin]?.value != null));
  const missing = matrix.filter(row => !products.some(p => row.values?.[p.parent_asin]?.value != null));
  heading(ui.comparisonTable, 'Listed facts');
  if (known.length) ui.comparisonTable.append(tableFor(known));
  if (missing.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = `Details not supplied (${missing.length})`;
    details.append(summary, tableFor(missing)); ui.comparisonTable.append(details);
  }
  const addPoints = (parent, points, prefix = '') => {
    if (!points?.length) return;
    const list = document.createElement('ul');
    points.forEach(point => {
      const item = document.createElement('li'); item.textContent = prefix + point.text;
      sourceDetails(item, point.evidence_refs); list.append(item);
    });
    parent.append(list);
  };
  const objective = result.objective_comparison || {};
  const assessments = objective.product_assessments || [];
  if (assessments.some(row => row.pros?.length || row.cons?.length) || objective.trade_offs?.length) {
    heading(ui.comparisonDetails, 'What stands out');
    assessments.forEach(row => {
      if (!row.pros?.length && !row.cons?.length) return;
      heading(ui.comparisonDetails, titleById.get(row.parent_asin) || row.parent_asin);
      addPoints(ui.comparisonDetails, row.pros);
      addPoints(ui.comparisonDetails, row.cons, 'Consider: ');
    });
    addPoints(ui.comparisonDetails, objective.trade_offs);
  }
  const personal = result.personalized_comparison || {};
  if (personal.personalization_applied && personal.products?.some(row => row.fit_reasons?.length || row.cautions?.length)) {
    const section = document.createElement('section'); section.className = 'personalized-comparison';
    heading(section, 'For your preferences');
    personal.products.forEach(row => {
      if (!row.fit_reasons?.length && !row.cautions?.length) return;
      heading(section, titleById.get(row.parent_asin) || row.parent_asin);
      addPoints(section, row.fit_reasons); addPoints(section, row.cautions, 'Check first: ');
    });
    ui.comparisonDetails.append(section);
  }
  ui.comparisonNote.textContent = result.status === 'offline_preview'
    ? 'Showing direct catalog facts. Personalized explanations are available when a model is configured.'
    : result.status === 'partial'
      ? 'Some explanations could not be completed. The available details are shown; missing values remain unknown.'
      : 'Based on the supplied listings. Inferences are labeled; unknown details still need checking.';
  return true;
}

function renderComparison(handoff) {
  ui.comparisonTable.replaceChildren();
  ui.comparisonDetails.replaceChildren();
  ui.comparisonNote.textContent = '';
  if (renderDescriptionComparison(handoff)) return;
  const summary = handoff?.comparison_summary;
  const rows = summary?.rows || [];
  ui.comparison.hidden = rows.length === 0;
  ui.comparisonTable.replaceChildren();
  ui.comparisonNote.textContent = rows.length ? summary.note || "" : "";
  if (!rows.length) return;

  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  const metricHead = document.createElement("th");
  metricHead.scope = "col";
  metricHead.textContent = "METRIC";
  headRow.append(metricHead);
  rows.forEach((row) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = row.title;
    headRow.append(cell);
  });
  head.append(headRow);
  table.append(head);

  const body = document.createElement("tbody");
  const metrics = [
    ["PRICE", (row) => row.price == null ? "UNKNOWN" : `$${Number(row.price).toFixed(2)}${row.parent_asin === summary.lowest_price_asin ? " · LOWEST" : ""}`],
    ["RATING", (row) => row.rating == null ? "UNKNOWN" : `★ ${row.rating}${row.parent_asin === summary.highest_rating_asin ? " · HIGHEST" : ""}`],
    ["HARD COVERAGE", (row) => row.hard_coverage == null ? "UNKNOWN" : `${Math.round(Number(row.hard_coverage) * 100)}%`],
    ["SUPPORTED", (row) => String(row.supported_count ?? 0)],
    ["TRADE-OFFS", (row) => String(row.tradeoff_count ?? 0)],
    ["UNKNOWNS", (row) => String(row.unknown_count ?? 0)],
  ];
  metrics.forEach(([label, format]) => {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.scope = "row";
    th.textContent = label;
    tr.append(th);
    rows.forEach((row) => {
      const td = document.createElement("td");
      td.textContent = format(row);
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(body);
  ui.comparisonTable.append(table);
}

function renderScenarios() {
  ui.scenarios.replaceChildren();
  scenarios.forEach((scenario) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "scenario-button";
    button.classList.toggle("active", activeScenario?.id === scenario.id);
    button.textContent = `↗ ${scenario.label}`;
    button.title = scenario.title;
    button.addEventListener("click", () => {
      activeScenario = scenario;
      scenarioStep = 0;
      ui.message.value = scenario.title;
      renderScenarios();
      renderStoryGuide();
      ui.message.focus();
    });
    ui.scenarios.append(button);
  });
}

function storyPrompts() {
  return activeScenario ? [activeScenario.title, ...(activeScenario.followups || [])] : [];
}

function renderStoryGuide() {
  const prompts = storyPrompts();
  ui.storyGuide.hidden = !activeScenario;
  if (!activeScenario) return;
  const complete = scenarioStep >= prompts.length;
  ui.storyName.textContent = activeScenario.label.toUpperCase();
  ui.storyProgress.textContent = complete ? "STORY COMPLETE" : `DEMO STORY ${scenarioStep + 1} / ${prompts.length}`;
  ui.storyPrompt.textContent = complete
    ? "All scripted turns completed. Continue freely or start another story."
    : prompts[scenarioStep];
  ui.storyNext.disabled = complete;
  ui.storyNext.textContent = complete ? "COMPLETE ✓" : "LOAD THIS TURN ↗";
}

function resetVisuals() {
  ui.clearShortlist.disabled = false;
  currentTurn = 0;
  ui.message.placeholder = "e.g. Blue instead — and no budget limit";
  ui.turnLabel.textContent = maxTurns == null ? '0 messages' : `0 / ${maxTurns}`;
  ui.turnProgress.parentElement.hidden = maxTurns == null;
  ui.turnProgress.style.width = "0%";
  ui.products.replaceChildren();
  ui.resultsHead.hidden = true;
  shortlisted.clear();
  currentSelectionState = { status: "draft", finalized: false };
  renderComparison(null);
  activeScenario = null;
  scenarioStep = 0;
  renderStoryGuide();
  renderShortlist();
  ui.chat.querySelectorAll(".message:not(.intro-message)").forEach((message) => message.remove());
  ui.turnAudit.replaceChildren();
  const auditNote = document.createElement("li");
  const auditTitle = document.createElement("span");
  const auditText = document.createElement("p");
  auditTitle.textContent = "READY";
  auditText.textContent = "Each completed turn will leave a bounded receipt here.";
  auditNote.append(auditTitle, auditText);
  ui.turnAudit.append(auditNote);
  renderState({ hard: {}, soft: {}, excluded: {}, state_changes: [], intent_version: 1 });
  renderReceipt({ timings: [], shown_count: "—", questions_asked: "—" });
  renderReplyOptions();
}

async function startSession() {
  sessionUsable = false;
  sessionId = null;
  setStatus("WARMING UP", "busy");
  ui.submit.disabled = true;
  ui.exportAudit.disabled = true;
  try {
    const data = await api("/api/session", {});
    sessionId = data.session_id;
    maxTurns = data.max_turns;
    sessionUsable = true;
    scenarios = data.scenarios;
    const modeLabel = data.orchestration_mode === "adaptive" ? "PRODUCT MODE" : "SCORE-COMPAT MODE";
    const modelLabel = data.model_provider === "off" ? "LOCAL RULES" : `${data.model_provider.toUpperCase()} ASSIST`;
    const catalogLabel = data.search_backend === "search_tool" ? "PRODUCT SEARCH" : (data.catalog_products === 50000 ? "50K CATALOG" : `${data.catalog_products} PRODUCT DEMO`);
    ui.runtimeMode.lastChild.textContent = ` ${catalogLabel} · ${modeLabel} · ${modelLabel}`;
    ui.dataBoundary.textContent = data.data_disclosure;
    ui.dataBoundary.classList.toggle("cloud", Boolean(data.cloud_model));
    resetVisuals();
    renderScenarios();
    setStatus("READY", "live");
    ui.exportAudit.disabled = false;
    ui.message.focus();
  } catch (error) {
    setStatus("OFFLINE", "");
    addMessage("agent", error.message);
  } finally {
    ui.submit.disabled = !sessionUsable;
  }
}

ui.composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = ui.message.value.trim();
  if (!message || !sessionId || !sessionUsable || ui.submit.disabled) return;
  addMessage("user", message);
  ui.message.value = "";
  ui.submit.disabled = true;
  syncReplyOptions();
  ui.newSession.disabled = true;
  setStatus("THINKING", "busy");
  try {
    const data = await api("/api/chat", { session_id: sessionId, message });
    currentTurn = data.turn;
    if (data.receipt.selection_reset) {
      shortlisted.clear();
      renderShortlist();
    }
    syncSelection(data.selection_state, data.products);
    applyComparisonAssist(data.handoff);
    renderComparison(data.handoff);
    addMessage("agent", data.assistant.message, data.assistant.ask_attribute);
    renderProducts(data.products, data.receipt?.display_mode || false, data.shopping_guide);
    renderState(data.receipt);
    renderReceipt(data.receipt);
    renderReplyOptions(data.receipt);
    appendTurnAudit(data);
    const expectedPrompt = storyPrompts()[scenarioStep];
    if (expectedPrompt && message === expectedPrompt) scenarioStep += 1;
    renderStoryGuide();
    ui.turnLabel.textContent = maxTurns == null ? `${currentTurn} messages` : `${currentTurn} / ${maxTurns}`;
    ui.turnProgress.style.width = maxTurns == null ? '0%' : `${currentTurn / maxTurns * 100}%`;
    setStatus(data.products.length ? `${data.products.length} MATCHES` : "LISTENING", "live");
    if (data.remaining_turns === 0) {
      sessionUsable = false;
      ui.submit.disabled = true;
      ui.message.placeholder = "Turn limit reached — start a new session";
    }
  } catch (error) {
    addMessage("agent", error.message);
    if (["session_expired", "session_not_found", "turn_limit"].includes(error.code)) {
      sessionUsable = false;
      setStatus(error.code === "turn_limit" ? "TURN LIMIT" : "SESSION EXPIRED", "");
      ui.message.placeholder = "Start a new session to continue";
    } else {
      setStatus("TRY AGAIN", "");
    }
  } finally {
    if (sessionUsable && (maxTurns == null || currentTurn < maxTurns)) ui.submit.disabled = false;
    ui.newSession.disabled = false;
    syncReplyOptions();
    ui.message.focus();
  }
});

ui.message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    ui.composer.requestSubmit();
  }
});
ui.message.addEventListener("input", syncReplyOptions);
ui.newSession.addEventListener("click", startSession);
ui.storyNext.addEventListener("click", () => {
  const prompt = storyPrompts()[scenarioStep];
  if (!prompt) return;
  ui.message.value = prompt;
  ui.message.focus();
});
ui.exportAudit.addEventListener("click", async () => {
  if (!sessionId) return;
  ui.exportAudit.disabled = true;
  try {
    const audit = await api("/api/audit", { session_id: sessionId });
    const blob = new Blob([JSON.stringify(audit, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = `show-me-your-agent-${sessionId}.json`;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch (error) {
    addMessage("agent", `Audit export failed: ${error.message}`);
  } finally {
    ui.exportAudit.disabled = false;
  }
});
ui.clearShortlist.addEventListener("click", async () => {
  if (!sessionUsable || !sessionId) return;
  const requestSession = sessionId;
  ui.clearShortlist.disabled = true;
  try {
    const state = await api("/api/select", { session_id: requestSession, clear: true });
    if (sessionId !== requestSession) return;
    syncSelection(state, []);
    renderComparison(null);
    ui.products.querySelectorAll(".shortlist-button").forEach((button) => {
      button.classList.remove("selected");
      button.setAttribute("aria-pressed", "false");
      button.disabled = false;
      button.textContent = "+ SHORTLIST";
    });
  } catch (error) {
    if (sessionId !== requestSession) return;
    addMessage("agent", `Could not clear selection: ${error.message}`);
  } finally {
    if (sessionId === requestSession) ui.clearShortlist.disabled = false;
  }
});
ui.exportSelection.addEventListener("click", async () => {
  if (!sessionUsable || !sessionId || shortlisted.size === 0) return;
  const requestSession = sessionId;
  ui.exportSelection.disabled = true;
  try {
    const handoff = await api("/api/handoff", { session_id: requestSession });
    if (sessionId !== requestSession) return;
    renderComparison(handoff);
    const blob = new Blob([JSON.stringify(handoff, null, 2)], { type: "application/json" });
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = `show-me-your-agent-selection-${sessionId}.json`;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  } catch (error) {
    if (sessionId !== requestSession) return;
    addMessage("agent", `Selection export failed: ${error.message}`);
  } finally {
    if (sessionId === requestSession) ui.exportSelection.disabled = shortlisted.size === 0;
  }
});
ui.finalizeSelection.addEventListener("click", () => {
  if (!sessionId || shortlisted.size === 0) return;
  ui.message.value = "Finalize my selection.";
  ui.message.focus();
});
function loadSelectionHistoryCommand(direction) {
  if (!sessionUsable || ui.submit.disabled || !currentSelectionState[`can_${direction}_selection`]) return;
  if (ui.message.value.trim()) {
    ui.message.focus();
    return;
  }
  ui.message.value = direction === "undo" ? "Undo selection" : "Redo selection";
  ui.message.dispatchEvent(new Event("input"));
  ui.message.focus();
}
ui.undoShortlist.addEventListener("click", () => loadSelectionHistoryCommand("undo"));
ui.redoShortlist.addEventListener("click", () => loadSelectionHistoryCommand("redo"));
startSession();

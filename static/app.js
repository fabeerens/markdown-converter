/* ==========================================================================
   Markdown converter — front-end.

   Geen framework en geen build-stap: één expliciete `state`, en per gebied een
   render-functie die die state naar de DOM schrijft. Alles wat de gebruiker
   verandert gaat eerst in `state` en dan door een render — nooit rechtstreeks
   de DOM patchen. Dat is precies waar de vorige versie fragiel werd: daar
   stonden dezelfde gegevens op drie plekken (variabele, DOM-waarde, en het
   document zelf) en liepen die uit elkaar bij het wisselen van tabblad.
   ========================================================================== */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/* --------------------------------------------------------------------------
   State
   -------------------------------------------------------------------------- */

const state = {
  /** Welk bron-tabblad actief is: "jur" | "wet" | "doc" | "tekst". */
  tab: "jur",
  /** Is er een OpenRouter-sleutel? Bepaalt of het opschoonpaneel zin heeft. */
  llmAvailable: false,
  /** Alle opgehaalde documenten. */
  docs: [],
  /** Id van het document dat de editor toont. */
  activeId: null,
  /** Oplopende teller voor document-id's. */
  nextId: 1,
  /** Laatst opgehaalde instellingen (incl. `defaults`), voor de reset-knoppen. */
  settings: null,
};

const LANGS = ["NL", "EN", "FR", "DE", "ES", "IT", "PT", "PL"];

const PLACEHOLDERS = {
  jur: "ECLI of link — bv. ECLI:EU:C:2025:645 · ECLI:NL:HR:2012:BQ9251 · ECLI:CE:ECHR:… · HUDOC-link",
  wet: "link, CELEX of BWB — bv. 32016R0679 · eur-lex.europa.eu/eli/… · BWBR0040940",
  doc: "https://… (link naar een PDF, Word, Excel …)",
};

/** Het document dat nu in de editor staat. */
function activeDoc() {
  return state.docs.find((d) => d.id === state.activeId) || null;
}

/** Het opschoonprofiel van een document: expliciete keuze, anders de soort. */
function profileFor(doc) {
  return doc.obsidian ? "obsidian" : doc.kind;
}

function addDoc({
  title, filenameBase, source, kind, markdown, allowObsidian,
  attachments_token, attachment_count,
}) {
  const doc = {
    id: state.nextId++,
    title,
    filenameBase: filenameBase || title,
    source,
    kind: kind === "caselaw" ? "caselaw" : "generic",
    // Bij automatisch herkende rechtspraak (bv. een ECLI) is opmaken voor
    // Obsidian altijd zinvol. Bij een geüpload document of geplakte tekst
    // weet de tool niet of het om een uitspraak gaat — de gebruiker mag dat
    // daar zelf aangeven (`allowObsidian`, meegegeven vanuit die tabbladen).
    allowObsidian: kind === "caselaw" || Boolean(allowObsidian),
    obsidian: false,
    model: $("#model").value || null,
    markdown,
    cleaned: false,
    translated: false,
    lastUsage: null,
    // Alleen gezet als bij Documentupload (PDF) losse afbeeldingen zijn
    // geëxtraheerd — bepaalt of "Download" een .zip met attachments/-map
    // bouwt i.p.v. een los .md-bestand.
    attachmentsToken: attachments_token || null,
    attachmentCount: attachment_count || 0,
  };
  state.docs.push(doc);
  setActive(doc.id);
  return doc;
}

/** Bewaar wat de gebruiker in het tekstvak heeft getypt vóór we wisselen. */
function saveEdits() {
  const doc = activeDoc();
  if (doc) doc.markdown = $("#md").value;
}

function setActive(id) {
  saveEdits();
  state.activeId = id;
  renderDocTabs();
  renderEditor();
}

function closeDoc(id) {
  const i = state.docs.findIndex((d) => d.id === id);
  if (i === -1) return;
  state.docs.splice(i, 1);
  if (state.activeId === id) {
    const neighbour = state.docs[i] || state.docs[i - 1];
    state.activeId = neighbour ? neighbour.id : null;
  }
  renderDocTabs();
  renderEditor();
}

/* --------------------------------------------------------------------------
   Statusregel
   -------------------------------------------------------------------------- */

function setStatus(message, variant = "info", { busy = false, detail = "" } = {}) {
  const el = $("#status");
  el.className = `status show ${variant}`;
  el.innerHTML = "";
  if (busy) {
    const spin = document.createElement("div");
    spin.className = "spinner";
    el.appendChild(spin);
  }
  const text = document.createElement("div");
  text.textContent = message;
  if (detail) {
    const d = document.createElement("span");
    d.className = "detail";
    d.textContent = detail;
    text.appendChild(d);
  }
  el.appendChild(text);
}

function clearStatus() {
  $("#status").className = "status";
}

/* --------------------------------------------------------------------------
   Verzoeken
   -------------------------------------------------------------------------- */

async function api(url, options) {
  const r = await fetch(url, options);
  const data = await r.json().catch(() => ({ error: "Onverwacht antwoord van de server." }));
  if (!r.ok) throw new Error(data.error || `Fout ${r.status}`);
  return data;
}

const postJSON = (url, body) =>
  api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/* --------------------------------------------------------------------------
   Bron-tabs
   -------------------------------------------------------------------------- */

function renderTabs() {
  let selectedTab = null;
  $$(".tab").forEach((tab) => {
    const selected = tab.dataset.tab === state.tab;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected) selectedTab = tab;
  });
  $$(".pane").forEach((pane) => {
    pane.hidden = pane.dataset.pane !== state.tab;
  });
  moveTabsIndicator(selectedTab);
}

// De glazen indicator vloeit naar het actieve tabblad toe (transform, geen
// left/top — dat blijft compositor-vriendelijk). Berekend uit de eigen
// afmetingen van het tabblad, dus werkt bij elke schermbreedte vanzelf mee.
function moveTabsIndicator(selectedTab) {
  const indicator = $("#tabs-indicator");
  const container = $(".tabs");
  if (!indicator || !container || !selectedTab) return;
  const cRect = container.getBoundingClientRect();
  const tRect = selectedTab.getBoundingClientRect();
  indicator.style.width = `${tRect.width}px`;
  indicator.style.transform = `translateX(${tRect.left - cRect.left}px)`;
}

function initTabs() {
  const tabs = $$(".tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      state.tab = tab.dataset.tab;
      renderTabs();
    });
    // Pijltjestoetsen door de tablist, zoals een tablist zich hoort te gedragen.
    tab.addEventListener("keydown", (e) => {
      const i = tabs.indexOf(tab);
      const next =
        e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length]
        : e.key === "ArrowLeft" ? tabs[(i - 1 + tabs.length) % tabs.length]
        : null;
      if (!next) return;
      e.preventDefault();
      state.tab = next.dataset.tab;
      renderTabs();
      next.focus();
    });
  });
  renderTabs();
  // Lettertype/lay-out kan na de eerste render nog verschuiven (webfont,
  // scrollbar); positioneer de indicator dan één keer opnieuw. Bij een
  // schermbreedte-wijziging (bv. device-rotatie) idem.
  window.addEventListener("load", () => renderTabs());
  window.addEventListener("resize", () => renderTabs());
}

/* --------------------------------------------------------------------------
   Herhaalbare invoerrijen
   -------------------------------------------------------------------------- */

function makeRow(kind, onSubmit) {
  const row = document.createElement("div");
  row.className = "row";

  const field = document.createElement("div");
  field.className = "field";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = PLACEHOLDERS[kind];
  input.setAttribute("aria-label", kind === "doc" ? "Link naar een bestand" : "ECLI, CELEX of link");
  field.appendChild(input);
  row.appendChild(field);

  if (kind !== "doc") {
    const select = document.createElement("select");
    select.className = "select lang";
    select.setAttribute("aria-label", "Taal");
    LANGS.forEach((code) => {
      const opt = document.createElement("option");
      opt.textContent = code;
      select.appendChild(opt);
    });
    row.appendChild(select);
  }

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn btn-ghost btn-icon btn-danger";
  remove.title = "Deze regel verwijderen";
  remove.setAttribute("aria-label", "Deze regel verwijderen");
  remove.textContent = "✕";
  remove.addEventListener("click", () => {
    // Er blijft altijd minstens één rij staan, anders is er niets in te vullen.
    if (row.parentElement.children.length > 1) row.remove();
    else input.value = "";
    input.focus();
  });
  row.appendChild(remove);

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onSubmit();
    }
  });
  return row;
}

function initRows(kind, onSubmit) {
  const rows = $(`#rows-${kind}`);
  const add = () => {
    const row = makeRow(kind, onSubmit);
    rows.appendChild(row);
    return row;
  };
  add();
  $(`#add-${kind}`).addEventListener("click", () => {
    add().querySelector("input").focus();
  });
}

function readRows(kind) {
  return $$(`#rows-${kind} .row`)
    .map((row) => ({
      query: row.querySelector("input").value.trim(),
      lang: row.querySelector(".lang")?.value || "NL",
    }))
    .filter((r) => r.query);
}

/* --------------------------------------------------------------------------
   Ophalen — alle rijen parallel, één mislukking blokkeert de rest niet
   -------------------------------------------------------------------------- */

/**
 * Verwerk een lijst taken parallel en rapporteer voortgang en deelmislukkingen.
 * @param {Array} items       de op te halen dingen
 * @param {Function} label    item → naam voor de foutmelding
 * @param {Function} run      item → Promise die een document toevoegt
 * @param {string} noun       "opgehaald" / "geconverteerd"
 */
async function runBatch(items, label, run, noun) {
  let done = 0;
  const failures = [];
  const tick = () => setStatus(`Bezig: ${done}/${items.length} ${noun}…`, "info", { busy: true });
  tick();

  await Promise.allSettled(
    items.map(async (item) => {
      try {
        await run(item);
      } catch (e) {
        failures.push(`${label(item)} — ${e.message}`);
      } finally {
        done += 1;
        tick();
      }
    })
  );

  if (!failures.length) {
    setStatus(items.length === 1 ? "Klaar." : `${items.length} documenten ${noun}.`, "ok");
  } else {
    const ok = items.length - failures.length;
    setStatus(
      `${ok} van ${items.length} ${noun}.`,
      ok === 0 ? "err" : "info",
      { detail: `Mislukt: ${failures.join(" · ")}` }
    );
  }
}

function withBusyButton(button, work) {
  button.disabled = true;
  return work().finally(() => {
    button.disabled = false;
  });
}

async function fetchLinks(kind) {
  const items = readRows(kind);
  if (!items.length) {
    setStatus("Voer minstens één ECLI, CELEX of link in.", "err");
    return;
  }
  await withBusyButton($(`#fetch-${kind}`), () =>
    runBatch(
      items,
      (item) => item.query,
      async (item) => {
        const data = await postJSON("/api/convert/link", item);
        const name = deriveName(item.query);
        addDoc({ title: name, filenameBase: name, ...data });
      },
      "opgehaald"
    )
  );
}

/** Voor PDF's: losse ingesloten afbeeldingen (grafieken, screenshots) meenemen als bijlagen. */
function extractImagesRequested() {
  const el = $("#extract-images");
  return el && !el.closest("[hidden]") && el.checked;
}

async function fetchFileUrls() {
  const items = readRows("doc");
  if (!items.length) {
    setStatus("Plak minstens één link naar een bestand.", "err");
    return;
  }
  const extractImages = extractImagesRequested() || undefined;
  await withBusyButton($("#fetch-doc"), () =>
    runBatch(
      items,
      (item) => item.query,
      async (item) => {
        const data = await postJSON("/api/convert/file-url", { url: item.query, extract_images: extractImages });
        const base = basename(item.query);
        addDoc({ title: base, filenameBase: base, ...data, allowObsidian: true });
      },
      "opgehaald"
    )
  );
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const extractImages = extractImagesRequested();
  await runBatch(
    files,
    (file) => file.name,
    async (file) => {
      const form = new FormData();
      form.append("file", file);
      if (extractImages) form.append("extract_images", "1");
      const data = await api("/api/convert/file", { method: "POST", body: form });
      const base = file.name.replace(/\.[^.]+$/, "") || "document";
      addDoc({ title: base, filenameBase: base, ...data, allowObsidian: true });
    },
    "geconverteerd"
  );
}

/* --------------------------------------------------------------------------
   Tekst plakken — één contenteditable vak i.p.v. herhaalbare rijen: de
   gebruiker plakt of typt hier zelf, dus een batch van meerdere rijen past
   niet bij deze invoervorm. `innerHTML` (verrijkt) én `innerText` (kaal)
   gaan beide mee; de server kiest welke bruikbaar is.
   -------------------------------------------------------------------------- */

/**
 * Plakt rechtstreeks vanaf het systeemklembord, zonder dat de gebruiker zelf
 * Cmd/Ctrl+V hoeft te doen. `clipboard.read()` geeft — als de browser en de
 * herkomst van de tekst dat aanbieden — zowel `text/html` (verrijkt) als
 * `text/plain`; is er geen HTML-variant, dan valt de methode terug op
 * `clipboard.readText()`. Vereist een secure context (https/localhost) en
 * kan door de browser om toestemming vragen bij het eerste gebruik.
 */
async function pasteFromClipboard() {
  const el = $("#paste-area");
  try {
    let html = "";
    let text = "";
    if (navigator.clipboard.read) {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        if (!html && item.types.includes("text/html")) {
          html = await (await item.getType("text/html")).text();
        }
        if (!text && item.types.includes("text/plain")) {
          text = await (await item.getType("text/plain")).text();
        }
      }
    }
    if (!html && !text) text = await navigator.clipboard.readText();
    if (!html && !text) {
      setStatus("Het klembord bevat geen tekst.", "err");
      return;
    }
    el.innerHTML = html || "";
    if (!html) el.textContent = text;
    el.focus();
  } catch {
    setStatus(
      "Kon niet bij het klembord (browser weigerde toegang) — plak handmatig met Cmd/Ctrl+V.",
      "err"
    );
  }
}

async function fetchPastedText() {
  const el = $("#paste-area");
  const html = el.innerHTML.trim();
  const text = el.innerText.trim();
  if (!html && !text) {
    setStatus("Plak eerst tekst in het vak.", "err");
    return;
  }
  await withBusyButton($("#fetch-tekst"), () =>
    runBatch(
      [{ html, text }],
      () => "geplakte tekst",
      async (item) => {
        const data = await postJSON("/api/convert/text", item);
        const name = deriveName(item.text);
        addDoc({ title: name, filenameBase: name, ...data, allowObsidian: true });
        el.innerHTML = "";
      },
      "opgemaakt"
    )
  );
}

/** Een bestandsnaam voor de download, afgeleid uit de ingevoerde identifier. */
function deriveName(query) {
  const ecli = query.match(/ECLI:[A-Z]{2}:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+/i);
  if (ecli) return ecli[0].replace(/:/g, "-");
  const bwb = query.match(/BWB[A-Z]\d+/i);
  if (bwb) return bwb[0].toUpperCase();
  const hudoc = query.match(/\b00\d-\d{3,}\b/);
  if (hudoc) return `HUDOC-${hudoc[0]}`;
  const celex = query.match(/([0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4})/i);
  return celex ? celex[1].toUpperCase() : "document";
}

function basename(url) {
  const last = url.split("?")[0].split("#")[0].split("/").pop() || "document";
  return last.replace(/\.[^.]+$/, "") || "document";
}

/* --------------------------------------------------------------------------
   Documenttabs
   -------------------------------------------------------------------------- */

function renderDocTabs() {
  const wrap = $("#doc-tabs");
  wrap.hidden = state.docs.length === 0;
  wrap.replaceChildren(
    ...state.docs.map((doc) => {
      const tab = document.createElement("div");
      tab.className = "doc-tab glass";
      tab.setAttribute("aria-current", String(doc.id === state.activeId));

      const label = document.createElement("button");
      label.type = "button";
      label.className = "label";
      label.style.cssText = "background:none;border:0;color:inherit;font:inherit;cursor:pointer;padding:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
      label.textContent = doc.title;
      label.title = doc.source;
      label.addEventListener("click", () => setActive(doc.id));
      tab.appendChild(label);

      if (doc.cleaned) {
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.textContent = "AI";
        badge.title = "Opgeschoond met AI";
        tab.appendChild(badge);
      }

      const close = document.createElement("button");
      close.type = "button";
      close.className = "close";
      close.title = `${doc.title} sluiten`;
      close.setAttribute("aria-label", `${doc.title} sluiten`);
      close.textContent = "✕";
      close.addEventListener("click", (e) => {
        e.stopPropagation();
        closeDoc(doc.id);
      });
      tab.appendChild(close);
      return tab;
    })
  );
}

/* --------------------------------------------------------------------------
   Editor en opschoonpaneel
   -------------------------------------------------------------------------- */

function renderEditor() {
  const doc = activeDoc();
  $("#output").hidden = !doc;
  if (!doc) return;

  $("#md").value = doc.markdown;
  $("#src").textContent = doc.source;
  $("#src").title = doc.source;
  updateLineNumbers();

  $("#download").textContent = doc.attachmentsToken
    ? `Download .zip (${doc.attachmentCount} afb.)`
    : "Download .md";

  // "Opmaken voor Obsidian" staat altijd bij automatisch herkende rechtspraak,
  // en ook bij Documentupload/Tekst plakken — daar kán het een uitspraak zijn
  // die de tool niet automatisch als zodanig herkent (bv. handmatig gevonden
  // omdat er nog geen bron voor dat land is).
  const obsidianWrap = $("#obsidian-wrap");
  obsidianWrap.hidden = !doc.allowObsidian;
  $("#obsidian").checked = doc.obsidian;

  const model = $("#model");
  if (doc.model && [...model.options].some((o) => o.value === doc.model)) model.value = doc.model;

  // Eén opschoon-/vertaalactie tegelijk per document (zie runClean()): twee
  // verschillende documenten mogen best gelijktijdig lopen — alleen hetzelfde
  // document nog een keer aanklikken terwijl het al bezig is, is geblokkeerd.
  // De knoppen/voortgangsbalk/Annuleren-knop zijn gedeelde DOM-elementen en
  // weerspiegelen dus altijd het document dat nu getoond wordt.
  const busyHere = activeCleans.has(doc.id);
  const button = $("#clean");
  button.disabled = doc.cleaned || busyHere;
  button.textContent = doc.cleaned ? "Opgeschoond ✓" : "Opschonen";

  const translateButton = $("#translate-nl");
  translateButton.disabled = doc.translated || busyHere;
  translateButton.textContent = doc.translated ? "Vertaald ✓" : "Vertalen naar het Nederlands";

  $("#cancel-clean").hidden = !busyHere;
  if (!busyHere) hideProgress();
  renderCleanResult(doc);

  $("#clean-title").textContent =
    doc.obsidian ? "Opmaken voor Obsidian"
    : doc.kind === "caselaw" ? "Opschonen met AI — uitspraak-opmaak"
    : "Opschonen met AI";

  $("#clean-panel").classList.toggle("show", state.llmAvailable);
  if (state.llmAvailable) refreshEstimate();
}

const fmt = (n) => n.toLocaleString("nl-NL");
const fmtCost = (usd) => `$${usd < 0.01 ? usd.toFixed(4) : usd.toFixed(3)}`;

/* --------------------------------------------------------------------------
   Voortgangsbalk en resultaat (tokens/kosten) van opschonen/vertalen
   -------------------------------------------------------------------------- */

/** Eén opschoon-/vertaalactie tegelijk per document: docId → {requestId, controller}.
 * Verschillende documenten mogen gelijktijdig lopen; hetzelfde document niet twee keer. */
const activeCleans = new Map();

function setProgress(producedTokens, expectedTokens) {
  $("#clean-progress").hidden = false;
  const pct = expectedTokens > 0 ? Math.min(97, (producedTokens / expectedTokens) * 100) : 0;
  $("#clean-progress-fill").style.width = `${pct}%`;
}

function hideProgress() {
  $("#clean-progress").hidden = true;
  $("#clean-progress-fill").style.width = "0%";
}

/** Toont het tokengebruik/kosten die OpenRouter voor de laatste actie op dit
 * document teruggaf — blijft staan totdat een volgende actie het overschrijft
 * of het document sluit, ook als je tussendoor van tabblad wisselt. */
function renderCleanResult(doc) {
  const el = $("#clean-result");
  if (!doc.lastUsage || !doc.lastUsage.usage.total_tokens) {
    el.hidden = true;
    return;
  }
  const { label, usage } = doc.lastUsage;
  const parts = [`${label}: ${fmt(usage.total_tokens)} tokens (${fmt(usage.prompt_tokens || 0)} invoer, ${fmt(usage.completion_tokens || 0)} uitvoer)`];
  if (usage.cost) parts.push(`${fmtCost(usage.cost)} (OpenRouter)`);
  el.textContent = parts.join(" · ");
  el.hidden = false;
}

let estimateToken = 0;

/** Vraag de kostenraming op. Alleen het laatste antwoord mag de UI bijwerken. */
async function refreshEstimate() {
  const doc = activeDoc();
  if (!doc) return;
  const token = ++estimateToken;
  const est = $("#estimate");
  est.textContent = "Kosten berekenen…";
  try {
    const data = await postJSON("/api/estimate", {
      markdown: $("#md").value,
      profile: profileFor(doc),
      model: $("#model").value,
    });
    if (token !== estimateToken) return; // een nieuwer verzoek is al onderweg
    const parts = [
      `${data.chunks} ${data.chunks === 1 ? "deel" : "delen"}`,
      // De documentgrootte, niet invoer+uitvoer opgeteld: dat laatste oogt twee
      // keer zo groot als het document werkelijk is.
      `~${fmt(data.input_tokens)} tokens invoer`,
    ];
    if (data.cost_usd != null) {
      parts.push(`≈ $${data.cost_usd < 0.01 ? data.cost_usd.toFixed(4) : data.cost_usd.toFixed(3)}`);
    }
    est.textContent = `${parts.join(" · ")} — ${data.model}`;
  } catch {
    if (token !== estimateToken) return;
    est.textContent = "Koppen naar markdown, losse regels samenvoegen, kop-/voetteksten verwijderen.";
  }
}

// Moet letterlijk gelijk zijn aan STREAM_ERROR_SENTINEL in mdconv/api.py: de
// HTTP-status is op dat moment al 200, dus een fout halverwege de stream (bv.
// een verbindingsstoring bij het tweede deel) kan alleen nog in de body zelf
// gemeld worden. Twee andere frametypes delen hetzelfde `\x00`-teken, maar
// zíjn afgesloten (zie `_frame()` in mdconv/api.py): CLEAN_PROGRESS en
// CLEAN_USAGE, elk gevolgd door JSON en een sluitende `\x00`.
const STREAM_ERROR_SENTINEL = "\x00CLEAN_ERROR\x00";
const FRAME_MARK = "\x00";

/**
 * Ontleedt een streaming-respons in platte tekst en control-frames, over de
 * grenzen van losse `reader.read()`-happen heen — een frame kan best
 * halverwege een netwerkhap doorlopen, dus alles wat nog niet compleet is
 * blijft in `buf` staan tot de volgende `push()`. CLEAN_ERROR is bewust de
 * enige niet-afgesloten variant (die is altijd het allerlaatste in de
 * stream): zodra hij gezien is, geldt de rest van elke volgende `push()` als
 * onderdeel van de foutmelding.
 */
function makeStreamParser({ onText, onProgress, onUsage }) {
  let buf = "";
  let errorMode = false;
  let errorMsg = null;
  return {
    push(chunkText) {
      if (errorMode) {
        errorMsg += chunkText;
        return;
      }
      buf += chunkText;
      for (;;) {
        const at = buf.indexOf(FRAME_MARK);
        if (at === -1) {
          if (buf) onText(buf);
          buf = "";
          return;
        }
        if (at > 0) {
          onText(buf.slice(0, at));
          buf = buf.slice(at);
        }
        const tagEnd = buf.indexOf(FRAME_MARK, 1);
        if (tagEnd === -1) return; // tag nog niet compleet binnen; wacht op meer
        const tag = buf.slice(1, tagEnd);
        if (tag === "CLEAN_ERROR") {
          errorMode = true;
          errorMsg = buf.slice(tagEnd + 1);
          buf = "";
          return;
        }
        const payloadEnd = buf.indexOf(FRAME_MARK, tagEnd + 1);
        if (payloadEnd === -1) return; // payload nog niet compleet binnen; wacht op meer
        const payload = buf.slice(tagEnd + 1, payloadEnd);
        buf = buf.slice(payloadEnd + 1);
        try {
          const data = JSON.parse(payload);
          if (tag === "CLEAN_PROGRESS") onProgress(data);
          else if (tag === "CLEAN_USAGE") onUsage(data);
        } catch {
          // Een niet te ontleden frame negeren we — de inhoud (tekst) gaat voor.
        }
      }
    },
    /** null = geen fout gezien; anders de (mogelijk lege) foutmelding. */
    finish() {
      return errorMsg;
    },
  };
}

/**
 * Kern van zowel "Opschonen" als "Vertalen naar het Nederlands": stream een
 * `/api/clean/stream`-aanroep in het tekstvak en schrijf het resultaat terug
 * naar het document. `guardField` voorkomt dubbel werk (bv. `cleaned` of
 * `translated`) en is per actie apart, zodat opschonen en vertalen elkaar
 * niet blokkeren — je kunt een document eerst vertalen én daarna nog
 * opschonen, of andersom. Verschillende documenten mogen gelijktijdig lopen
 * (`activeCleans`, per docId) — alleen hetzelfde document nog een keer
 * starten terwijl het al bezig is, is geblokkeerd. De voortgangsbalk en
 * Annuleren-knop zijn gedeelde DOM-elementen en tonen dus altijd het
 * document dat op dat moment in de editor staat.
 */
async function runClean(doc, profile, { guardField, resultLabel, busyText, doneText, sourceSuffix, failMessage }) {
  if (!doc || doc[guardField] || activeCleans.has(doc.id)) {
    if (activeCleans.has(doc.id)) {
      setStatus(
        `"${doc.title}" is al bezig — wacht tot dat klaar is, of annuleer eerst.`,
        "err"
      );
    }
    return;
  }
  saveEdits();

  const docId = doc.id;
  const isLive = () => state.activeId === docId; // gebruiker kan tijdens het wachten wisselen
  const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const controller = new AbortController();
  activeCleans.set(docId, { requestId, controller });
  if (isLive()) renderEditor(); // knoppen uit, Annuleren aan, voortgangsbalk klaarzetten

  setStatus(busyText, "info", { busy: true });

  try {
    const response = await fetch("/api/clean/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ markdown: doc.markdown, profile, model: $("#model").value, request_id: requestId }),
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Fout ${response.status}`);
    }

    // Live bijwerken terwijl de tekst binnenkomt — alleen als dit document nog
    // steeds getoond wordt; anders schrijven we alleen naar doc.markdown en
    // rendert de editor het geheel zodra de gebruiker terugschakelt.
    if (isLive()) $("#md").value = "";

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let acc = "";
    let usage = null;
    const parser = makeStreamParser({
      onText: (t) => {
        acc += t;
        if (isLive()) {
          $("#md").value = acc;
          updateLineNumbers();
        }
      },
      onProgress: (p) => {
        if (isLive()) setProgress(p.produced_tokens, p.expected_tokens);
      },
      onUsage: (u) => { usage = u; },
    });
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    const err = parser.finish();
    if (err !== null) throw new Error(err || failMessage);

    doc.markdown = acc.trim() + "\n";
    doc.source += sourceSuffix;
    doc[guardField] = true;
    doc.lastUsage = usage ? { label: resultLabel, usage } : doc.lastUsage;
    renderDocTabs();
    setStatus(doneText, "ok");
  } catch (e) {
    if (e.name === "AbortError") {
      setStatus(`"${doc.title}" geannuleerd.`, "info");
    } else {
      setStatus(e.message, "err");
      if (isLive()) {
        // De halfklare tekst terugzetten naar de laatst bewaarde staat, niet de
        // afgebroken streaming-tekst laten staan.
        $("#md").value = doc.markdown;
        updateLineNumbers();
      }
    }
  } finally {
    activeCleans.delete(docId);
    if (isLive()) renderEditor();
  }
}

/** Annuleert de lopende opschoon-/vertaalactie van het document dat nu in de
 * editor staat: meldt de server (best-effort, geen wachttijd) en breekt de
 * eigen fetch meteen af. */
function cancelActiveClean() {
  const doc = activeDoc();
  if (!doc) return;
  const entry = activeCleans.get(doc.id);
  if (!entry) return;
  postJSON("/api/clean/cancel", { request_id: entry.requestId }).catch(() => {});
  entry.controller.abort();
}

async function cleanActiveDoc() {
  const doc = activeDoc();
  if (!doc) return;
  await runClean(doc, profileFor(doc), {
    guardField: "cleaned",
    resultLabel: "Opschonen",
    busyText: `"${doc.title}" opschonen met AI… dit kan enkele minuten duren.`,
    doneText: `"${doc.title}" is opgeschoond.`,
    sourceSuffix: " • AI-opgeschoond",
    failMessage: "AI-opschoning mislukt.",
  });
}

async function translateActiveDoc() {
  const doc = activeDoc();
  if (!doc) return;
  await runClean(doc, "translate_nl", {
    guardField: "translated",
    resultLabel: "Vertalen",
    busyText: `"${doc.title}" vertalen naar het Nederlands… dit kan enkele minuten duren.`,
    doneText: `"${doc.title}" is vertaald naar het Nederlands.`,
    sourceSuffix: " • vertaald naar NL",
    failMessage: "Vertalen mislukt.",
  });
}

/* --------------------------------------------------------------------------
   Regelnummers

   Eén nummer per échte regel (per enter), niet per visueel omgebogen regel.
   Om exact uit te lijnen met hoe de textarea wrapt, wordt elke regel gemeten in
   een onzichtbare kloon met identieke breedte en lettertype.

   Twee dingen die de vorige versie traag maakten en hier zijn opgelost:
   de meting liep bij élke toetsaanslag (nu samengevoegd in één animatieframe),
   en hij liep ook als de tekst en breedte niet waren veranderd (nu overgeslagen
   via een cachesleutel).
   -------------------------------------------------------------------------- */

const editor = {
  textarea: null,
  gutter: null,
  mirror: null,
  key: "",
  frame: 0,
};

function syncGutterScroll() {
  editor.gutter.style.transform = `translateY(${-editor.textarea.scrollTop}px)`;
}

function updateLineNumbers() {
  cancelAnimationFrame(editor.frame);
  editor.frame = requestAnimationFrame(measureLineNumbers);
}

function measureLineNumbers() {
  const { textarea, gutter, mirror } = editor;
  const width = textarea.clientWidth;
  const value = textarea.value;

  // Niets veranderd aan tekst of breedte? Dan is de vorige meting nog geldig.
  const key = `${width}:${value.length}:${value}`;
  if (key === editor.key) return;
  editor.key = key;

  const lines = value.split("\n");
  mirror.style.width = `${width}px`;
  mirror.replaceChildren(
    ...lines.map((line) => {
      const div = document.createElement("div");
      // Een lege regel meet 0px hoog; een spatie geeft de echte regelhoogte.
      div.textContent = line.length ? line : " ";
      return div;
    })
  );

  // Eerst alles schrijven, dan alles lezen: zo kost de hele meting één layout
  // in plaats van er één per regel.
  const heights = [...mirror.children].map((div) => div.getBoundingClientRect().height);
  gutter.replaceChildren(
    ...heights.map((height, i) => {
      const div = document.createElement("div");
      div.style.height = `${height}px`;
      div.textContent = String(i + 1);
      return div;
    })
  );
  syncGutterScroll();
}

function initEditor() {
  editor.textarea = $("#md");
  editor.gutter = $("#gutter-inner");
  editor.mirror = $("#line-mirror");

  editor.textarea.addEventListener("input", updateLineNumbers);
  editor.textarea.addEventListener("scroll", syncGutterScroll, { passive: true });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(updateLineNumbers, 120);
  });
  // Het editor-blok is als geheel resizebaar; alleen de hoogte verandert, dus
  // de gemeten (breedte-afhankelijke) regelhoogtes blijven geldig.
  new ResizeObserver(syncGutterScroll).observe($("#editor"));
}

/* --------------------------------------------------------------------------
   Kopiëren en downloaden — werken op het actieve document
   -------------------------------------------------------------------------- */

async function copyActive() {
  saveEdits();
  const button = $("#copy");
  try {
    await navigator.clipboard.writeText($("#md").value);
    const original = button.textContent;
    button.textContent = "Gekopieerd ✓";
    setTimeout(() => {
      button.textContent = original;
    }, 1400);
  } catch {
    setStatus("Kopiëren naar het klembord is geweigerd door de browser.", "err");
  }
}

async function downloadActive() {
  const doc = activeDoc();
  if (!doc) return;
  saveEdits();
  // Zijn er losse afbeeldingen geëxtraheerd (Documentupload, PDF), dan bouwt
  // /api/download een .zip met de markdown + een attachments/-map i.p.v. een
  // los .md-bestand — zie mdconv/attachments.py.
  const response = await fetch("/api/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      markdown: doc.markdown, filename: doc.filenameBase,
      attachments_token: doc.attachmentsToken || undefined,
    }),
  });
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${doc.filenameBase}.${doc.attachmentsToken ? "zip" : "md"}`;
  link.click();
  URL.revokeObjectURL(link.href);
}

/* --------------------------------------------------------------------------
   Modelkeuze
   -------------------------------------------------------------------------- */

async function loadConfig() {
  try {
    const cfg = await api("/api/config");
    state.llmAvailable = Boolean(cfg.llm_available);
    // Alleen tonen als poppler-utils daadwerkelijk geïnstalleerd is (zie
    // pdf_images.available()) — anders een dode toggle die altijd faalt.
    $("#extract-images-wrap").hidden = !cfg.extract_images_available;
    const select = $("#model");
    const previous = select.value;
    select.replaceChildren(
      ...(cfg.models || []).map((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label || m.id;
        return opt;
      })
    );
    const remembered = localStorage.getItem("llmModel");
    for (const candidate of [previous, remembered]) {
      if (candidate && [...select.options].some((o) => o.value === candidate)) {
        select.value = candidate;
        break;
      }
    }
  } catch {
    state.llmAvailable = false;
  }
}

/* --------------------------------------------------------------------------
   Instellingen-dialoog
   -------------------------------------------------------------------------- */

const dialog = { lastFocus: null };

function modelRow(id = "", label = "") {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <div class="field" style="flex:0 0 44%"><input type="text" class="mid" placeholder="model-id" aria-label="Model-id"></div>
    <div class="field"><input type="text" class="mlabel" placeholder="label in de lijst" aria-label="Label"></div>
    <button type="button" class="btn btn-ghost btn-icon btn-danger" aria-label="Verwijderen">✕</button>`;
  row.querySelector(".mid").value = id;
  row.querySelector(".mlabel").value = label;
  row.querySelector("button").addEventListener("click", () => row.remove());
  return row;
}

function renderModelRows(models) {
  $("#settings-models").replaceChildren(...(models || []).map((m) => modelRow(m.id, m.label)));
}

async function openSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (e) {
    setStatus(e.message, "err");
    return;
  }
  const s = state.settings;
  renderModelRows(s.models);
  $("#settings-chunk").value = s.chunk_tokens;
  $("#settings-chunk").min = s.defaults.min_chunk_tokens;
  $("#settings-chunk").max = s.defaults.max_chunk_tokens;
  $("#settings-range").textContent =
    `tokens per deel (${fmt(s.defaults.min_chunk_tokens)}–${fmt(s.defaults.max_chunk_tokens)})`;
  $("#prompt-generic").value = s.prompts.generic;
  $("#prompt-caselaw").value = s.prompts.caselaw;
  $("#prompt-obsidian").value = s.prompts.obsidian;
  $("#prompt-translate_nl").value = s.prompts.translate_nl;
  $("#settings-msg").textContent = "";

  dialog.lastFocus = document.activeElement;
  $("#settings").classList.add("show");
  document.body.style.overflow = "hidden";
  $("#settings-close").focus();
}

function closeSettings() {
  $("#settings").classList.remove("show");
  document.body.style.overflow = "";
  dialog.lastFocus?.focus();
}

/** Houd de focus binnen de dialoog zolang die open staat. */
function trapFocus(e) {
  if (e.key !== "Tab") return;
  const focusable = $$(
    'button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])',
    $("#settings")
  ).filter((el) => !el.disabled && el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

async function saveSettings() {
  const models = $$("#settings-models .row").map((row) => ({
    id: row.querySelector(".mid").value.trim(),
    label: row.querySelector(".mlabel").value.trim(),
  })).filter((m) => m.id);

  const button = $("#settings-save");
  const msg = $("#settings-msg");
  button.disabled = true;
  try {
    await postJSON("/api/settings", {
      models,
      chunk_tokens: parseInt($("#settings-chunk").value, 10) || null,
      prompts: {
        generic: $("#prompt-generic").value,
        caselaw: $("#prompt-caselaw").value,
        obsidian: $("#prompt-obsidian").value,
        translate_nl: $("#prompt-translate_nl").value,
      },
    });
    await loadConfig();
    if (activeDoc() && state.llmAvailable) refreshEstimate();
    msg.className = "msg ok";
    msg.textContent = "Opgeslagen.";
    setTimeout(closeSettings, 500);
  } catch (e) {
    msg.className = "msg err";
    msg.textContent = e.message;
  } finally {
    button.disabled = false;
  }
}

function initSettings() {
  $("#open-settings").addEventListener("click", openSettings);
  $("#settings-close").addEventListener("click", closeSettings);
  $("#settings-cancel").addEventListener("click", closeSettings);
  $("#settings-save").addEventListener("click", saveSettings);
  $("#settings-add-model").addEventListener("click", () => {
    $("#settings-models").appendChild(modelRow()).querySelector("input").focus();
  });

  // Klik op de achtergrond sluit; klik in de dialoog niet.
  $("#settings").addEventListener("mousedown", (e) => {
    if (e.target === $("#settings")) closeSettings();
  });
  $("#settings").addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeSettings();
    } else {
      trapFocus(e);
    }
  });

  // Per veld terug naar de ingebouwde standaardwaarde — puur client-side, want
  // die standaarden zitten al in het antwoord van /api/settings.
  const resets = {
    "reset-models": () => renderModelRows(state.settings.defaults.models),
    "reset-chunk": () => { $("#settings-chunk").value = state.settings.defaults.chunk_tokens; },
    "reset-generic": () => { $("#prompt-generic").value = state.settings.defaults.prompts.generic; },
    "reset-caselaw": () => { $("#prompt-caselaw").value = state.settings.defaults.prompts.caselaw; },
    "reset-obsidian": () => { $("#prompt-obsidian").value = state.settings.defaults.prompts.obsidian; },
    "reset-translate_nl": () => {
      $("#prompt-translate_nl").value = state.settings.defaults.prompts.translate_nl;
    },
  };
  Object.entries(resets).forEach(([id, fn]) => $(`#${id}`).addEventListener("click", fn));
}

/* --------------------------------------------------------------------------
   Bestandsupload
   -------------------------------------------------------------------------- */

function initUpload() {
  const drop = $("#drop");
  const input = $("#file");

  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.classList.remove("over");
    uploadFiles(e.dataTransfer.files);
  });
  input.addEventListener("change", () => {
    uploadFiles(input.files);
    input.value = ""; // zodat hetzelfde bestand opnieuw gekozen kan worden
  });
}

/* --------------------------------------------------------------------------
   Opstarten
   -------------------------------------------------------------------------- */

function init() {
  initTabs();
  initEditor();
  initSettings();
  initUpload();

  initRows("jur", () => fetchLinks("jur"));
  initRows("wet", () => fetchLinks("wet"));
  initRows("doc", fetchFileUrls);

  $("#fetch-jur").addEventListener("click", () => fetchLinks("jur"));
  $("#fetch-wet").addEventListener("click", () => fetchLinks("wet"));
  $("#fetch-doc").addEventListener("click", fetchFileUrls);
  $("#fetch-tekst").addEventListener("click", fetchPastedText);
  $("#paste-clipboard").addEventListener("click", pasteFromClipboard);
  $("#clear-tekst").addEventListener("click", () => {
    $("#paste-area").innerHTML = "";
    $("#paste-area").focus();
  });

  $("#clean").addEventListener("click", cleanActiveDoc);
  $("#translate-nl").addEventListener("click", translateActiveDoc);
  $("#cancel-clean").addEventListener("click", cancelActiveClean);
  $("#copy").addEventListener("click", copyActive);
  $("#download").addEventListener("click", downloadActive);

  $("#obsidian").addEventListener("change", (e) => {
    const doc = activeDoc();
    if (!doc) return;
    doc.obsidian = e.target.checked;
    doc.cleaned = false; // ander profiel: opnieuw opschonen mag
    renderEditor();
  });

  $("#model").addEventListener("change", () => {
    localStorage.setItem("llmModel", $("#model").value);
    const doc = activeDoc();
    if (doc) doc.model = $("#model").value;
    if (doc && state.llmAvailable) refreshEstimate();
  });

  loadConfig();
  renderDocTabs();
  renderEditor();
}

document.addEventListener("DOMContentLoaded", init);

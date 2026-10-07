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

/* Draait deze installatie met AI (opschonen, vertalen, Obsidian, wiskunde-
   modus, instellingen)? Met `MDCONV_AI=off` laat de server die markup weg uit
   index.html (zie mdconv/features.py); dan mag de JS die elementen ook niet
   aanraken. Synchroon uit de pagina, niet uit /api/config: init() heeft het
   meteen nodig, vóórdat dat verzoek terug is. */
const AI_ENABLED = document.body.dataset.ai !== "off";
/* Het ⚙-paneel ontbreekt als AI via MDCONV_AI=off vergrendeld is. Staat AI
   alleen via de schakelaar uit, dan is het er wél, met alleen die schakelaar. */
const SETTINGS_ENABLED = Boolean($("#open-settings"));

/* --------------------------------------------------------------------------
   State
   -------------------------------------------------------------------------- */

const state = {
  /** Welk bron-tabblad actief is: "jur" | "wet" | "oo" | "doc" | "tekst". */
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
  /** Per tabblad: staat de lijst-invoer (één tekstvak) aan i.p.v. losse rijen? */
  bulk: {},
};

const LANGS = ["NL", "EN", "FR", "DE", "ES", "IT", "PT", "PL"];

const PLACEHOLDERS = {
  jur: "ECLI of link — bv. ECLI:EU:C:2025:645 · ECLI:NL:HR:2012:BQ9251 · ECLI:CE:ECHR:… · HUDOC-link",
  wet: "link, CELEX of BWB — bv. 32016R0679 · eur-lex.europa.eu/eli/… · BWBR0040940",
  oo: "kamerstuk of open overheid-document — bv. kst-36600-VII-1 · 36600-VII, nr. 1 · 2024D40329 · open.overheid.nl-link",
  doc: "https://… (link naar een PDF, Word, Excel …)",
};

/* Eén patroon per identificatievorm, gedeeld door de lijst-parser en
   deriveName(). Aan de serverkant leerde dit project al dat een los
   uitgeschreven CELEX-vorm uit elkaar loopt zodra de consolidatiedatum erbij
   komt (zie _CELEX_BODY in eurlex.py) — hier dus ook maar één plek.
   Let op de volgorde bij het testen: een geconsolideerde CELEX
   (02014R0910-20241018) matcht óók RE_HUDOC, andersom kan niet. */
const RE_URL = /https?:\/\/\S+/i;
const RE_ECLI = /ECLI:[A-Z]{2}:[A-Za-z0-9.]+:\d{4}:[A-Za-z0-9.]+/i;
const RE_BWB = /BWB[A-Z]\d+/i;
const RE_CELEX = /[0-9][0-9]{4}[A-Z]{1,2}[0-9]{2,4}(?:-[0-9]{8})?/i;
const RE_HUDOC = /\b00\d-\d{3,}\b/;
// Open overheid: publicatie-id's (kst-…, ah-tk-…, blg-…), het D-nummer van de
// Tweede Kamer en de id's van open.overheid.nl (UUID, ronl-…, oep-…).
const RE_KSTID = /\b(?:(?:kst|ah-tk|ah-ek|h-tk|h-ek)-[0-9A-Za-z]+(?:-[0-9A-Za-z]+)+|(?:blg|ah)-\d{4,}|kst-\d{6,}|(?:ronl|oep)-[0-9a-z-]+)\b/i;
const RE_UUID = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:_\d+)?\b/i;
const RE_DNUM = /\b\d{4}D\d{3,6}\b/i;

/* Op welke tabbladen een geplakte lijst zich uitsplitst over de invoerrijen.
   Uitbreiden = hier een tabblad bijzetten, de `.seg`/`.bulk`-markup in
   index.html toevoegen en initListMode() aanroepen in init(). Documentupload
   ("doc") heeft geen taalkeuze — de list-mode-helpers gaan met `#bulk-doc-lang`
   afwezig om. */
const LIST_PASTE_KINDS = new Set(["jur", "wet", "oo", "doc"]);

/** Enkelvoud/meervoud per tabblad, voor "18 regelingen herkend". */
const LIST_NOUN = {
  jur: ["document", "documenten"],
  wet: ["regeling", "regelingen"],
  oo: ["document", "documenten"],
  doc: ["link", "links"],
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
  attachments_token, attachment_count, bundle_token, warnings,
  batchIndex = 0, activate = true, ident = "",
}) {
  const doc = {
    id: state.nextId++,
    title,
    // Plaats in de aangeleverde lijst. Documenten komen in afrondingsvolgorde
    // binnen; hiermee zet runBatch() ze aan het eind terug in invoervolgorde.
    batchIndex,
    filenameBase: filenameBase || title,
    source,
    kind: kind === "caselaw" ? "caselaw" : "generic",
    // Bij automatisch herkende rechtspraak (bv. een ECLI) is opmaken voor
    // Obsidian altijd zinvol. Bij een geüpload document of geplakte tekst
    // weet de tool niet of het om een uitspraak gaat — de gebruiker mag dat
    // daar zelf aangeven (`allowObsidian`, meegegeven vanuit die tabbladen).
    allowObsidian: kind === "caselaw" || Boolean(allowObsidian),
    obsidian: false,
    model: $("#model")?.value || null,
    markdown,
    // Bronwaarschuwingen blijven bij hun eigen documenttab horen. Zo blijft
    // een weggelaten inhoudsafbeelding ook na wisselen van tab zichtbaar.
    warnings: Array.isArray(warnings) ? warnings : [],
    cleaned: false,
    translated: false,
    lastUsage: null,
    // Alleen gezet als bij Documentupload (PDF) losse afbeeldingen zijn
    // geëxtraheerd — bepaalt of "Download" een .zip met attachments/-map
    // bouwt i.p.v. een los .md-bestand.
    attachmentsToken: attachments_token || null,
    attachmentCount: attachment_count || 0,
    // Bronbytes en herkomst blijven op de server; alleen dit tijdelijke token
    // gaat door de browser en bouwt bij downloaden de uitpakbare kb-boom.
    bundleToken: bundle_token || null,
    // Open overheid: het id waaronder de bron dit document kent (om "al geopend" te herkennen).
    ident,
  };
  state.docs.push(doc);
  // Tijdens een batch niet meteen openen: dan zou de editor tijdens het
  // ophalen meespringen met de volgorde waarin de bronnen antwoorden.
  // De tab verschijnt wel meteen, zodat je de lijst ziet vollopen.
  if (activate) setActive(doc.id);
  else renderDocTabs();
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
  input.setAttribute(
    "aria-label",
    kind === "doc" ? "Link naar een bestand"
      : kind === "oo" ? "Kamerstuk, open overheid-document, identifier of link"
      : "ECLI, CELEX of link"
  );
  field.appendChild(input);
  row.appendChild(field);

  // Documentupload en Open overheid hebben geen taalkeuze (alleen Nederlands).
  if (kind !== "doc" && kind !== "oo") {
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

  // Een geplakte lijst splitst zich uit over de rijen — één Cmd/Ctrl+V en de
  // hele lijst staat er. Alleen bij een échte lijst: een gewone plak van één
  // regel (of ergens midden in een bestaande waarde) blijft een gewone plak.
  if (LIST_PASTE_KINDS.has(kind)) {
    input.addEventListener("paste", (e) => {
      const text = e.clipboardData ? e.clipboardData.getData("text/plain") : "";
      if (!text.includes("\n")) return;
      const { items, duplicates } = parseList(text);
      if (items.length < 2) return;
      e.preventDefault();
      spreadList(kind, row, items, duplicates);
    });
  }
  return row;
}

/** Per tabblad de submit-handler, zodat een rij die later ontstaat (met "+"
 *  of uit een geplakte lijst) dezelfde Enter-actie krijgt. */
const rowSubmit = {};

function initRows(kind, onSubmit) {
  rowSubmit[kind] = onSubmit;
  resetRows(kind, []);
  $(`#add-${kind}`).addEventListener("click", () => {
    const row = makeRow(kind, onSubmit);
    $(`#rows-${kind}`).appendChild(row);
    row.querySelector("input").focus();
  });
}

/** Vervangt alle rijen van een tabblad door één rij per item (minstens één). */
function resetRows(kind, items, lang) {
  const rows = $(`#rows-${kind}`);
  rows.replaceChildren();
  (items.length ? items : [""]).forEach((item) => {
    const row = makeRow(kind, rowSubmit[kind]);
    row.querySelector("input").value = item;
    const select = row.querySelector(".lang");
    if (select && lang) select.value = lang;
    rows.appendChild(row);
  });
}

/** Zet het eerste item in de rij waarin geplakt is en maakt voor elk volgend
 *  item een nieuwe rij eronder. De taalkeuze van die rij gaat mee: die gold
 *  blijkbaar voor deze lijst. */
function spreadList(kind, row, items, duplicates) {
  const lang = row.querySelector(".lang") ? row.querySelector(".lang").value : null;
  row.querySelector("input").value = items[0];
  let previous = row;
  items.slice(1).forEach((item) => {
    const next = makeRow(kind, rowSubmit[kind]);
    previous.after(next);
    next.querySelector("input").value = item;
    const select = next.querySelector(".lang");
    if (select && lang) select.value = lang;
    previous = next;
  });
  setStatus(`${listSummary(kind, items.length, duplicates)} — controleer en klik op Ophalen.`, "ok");
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
   Een geplakte lijst ontleden

   Vergevingsgezind, maar voorspelbaar: per regel één item, en wat er niet uit
   te halen valt gaat ongewijzigd door naar de server — die legt dan in het
   Nederlands uit wat er mis is. Stil weggooien is nooit goed.
   -------------------------------------------------------------------------- */

/** Eén regel uit een lijst → de identificatie die erin staat. */
function pickIdentifier(line) {
  const url = line.match(RE_URL);
  // Sluitleestekens van een markdown-link of een prozaregel horen niet bij de URL.
  if (url) return url[0].replace(/[.,;:!?)\]}>"'»]+$/, "");
  for (const pattern of [RE_KSTID, RE_DNUM, RE_UUID, RE_ECLI, RE_BWB, RE_CELEX, RE_HUDOC]) {
    const hit = line.match(pattern);
    if (hit) return hit[0];
  }
  // Geen bekende vorm: de regel zelf, zonder opsommingsteken of nummering.
  return line.replace(/^(?:[-*•·>]+|\d+[.)])\s+/, "").trim();
}

/**
 * Een geplakte lijst → losse items, plus hoeveel dubbele eruit gingen.
 * Lege regels en markdown-koppen (`## EU-wetgeving`) worden overgeslagen: een
 * lijst uit een notitie heeft die er vaak tussen staan.
 */
function parseList(text) {
  const items = [];
  const seen = new Set();
  let duplicates = 0;
  String(text).split(/\r?\n/).forEach((raw) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const item = pickIdentifier(line);
    if (!item) return;
    const key = item.toLowerCase();
    if (seen.has(key)) {
      duplicates += 1;
      return;
    }
    seen.add(key);
    items.push(item);
  });
  return { items, duplicates };
}

function listSummary(kind, count, duplicates) {
  const [one, many] = LIST_NOUN[kind];
  const parts = [`${count} ${count === 1 ? one : many} herkend`];
  if (duplicates) parts.push(`${duplicates} dubbele weggelaten`);
  return parts.join(" · ");
}

/* --------------------------------------------------------------------------
   Lijst plakken — één lijst, twee weergaven

   De losse rijen en het lijst-tekstvak zijn twee vensters op dezelfde lijst:
   bij het wisselen gaat de inhoud mee. Zo ben je nooit werk kwijt en leest
   "Ophalen" altijd wat je op dat moment ziet.
   -------------------------------------------------------------------------- */

function renderListMode(kind) {
  const bulk = Boolean(state.bulk[kind]);
  $(`#rows-${kind}`).hidden = bulk;
  $(`#bulk-${kind}`).hidden = !bulk;
  $(`#add-${kind}`).hidden = bulk;
  $(`#mode-${kind}-rows`).setAttribute("aria-pressed", String(!bulk));
  $(`#mode-${kind}-bulk`).setAttribute("aria-pressed", String(bulk));
  if (bulk) updateListCount(kind);
}

function switchListMode(kind, bulk) {
  if (Boolean(state.bulk[kind]) === bulk) return;
  const text = $(`#bulk-${kind}-text`);
  const langSelect = $(`#bulk-${kind}-lang`);
  if (bulk) {
    const rows = readRows(kind);
    if (rows.length) {
      text.value = rows.map((r) => r.query).join("\n");
      if (langSelect) langSelect.value = rows[0].lang;
    }
  } else {
    // Terug naar losse rijen: één rij per regel, met de taal van de lijst.
    // Documentupload heeft geen taalkeuze (`#bulk-doc-lang` bestaat niet).
    resetRows(kind, parseList(text.value).items, langSelect ? langSelect.value : null);
  }
  state.bulk[kind] = bulk;
  localStorage.setItem(`listMode:${kind}`, bulk ? "bulk" : "rows");
  renderListMode(kind);
  (bulk ? text : $(`#rows-${kind} input`)).focus();
}

/** Wat er in het tekstvak herkend wordt, live — zodat je ziet dat de plak
 *  goed geland is vóórdat je twintig verzoeken afvuurt. */
function updateListCount(kind) {
  const { items, duplicates } = parseList($(`#bulk-${kind}-text`).value);
  $(`#bulk-${kind}-count`).textContent =
    items.length ? listSummary(kind, items.length, duplicates) : "Eén per regel.";
}

function initListMode(kind) {
  // Documentupload heeft geen taalkeuze voor de lijst.
  const langSelect = $(`#bulk-${kind}-lang`);
  if (langSelect) {
    LANGS.forEach((code) => {
      const opt = document.createElement("option");
      opt.textContent = code;
      langSelect.appendChild(opt);
    });
  }
  $(`#mode-${kind}-rows`).addEventListener("click", () => switchListMode(kind, false));
  $(`#mode-${kind}-bulk`).addEventListener("click", () => switchListMode(kind, true));

  const text = $(`#bulk-${kind}-text`);
  text.addEventListener("input", () => updateListCount(kind));
  // In een tekstvak maakt Enter een nieuwe regel; ophalen is Cmd/Ctrl+Enter.
  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      rowSubmit[kind]();
    }
  });
  state.bulk[kind] = localStorage.getItem(`listMode:${kind}`) === "bulk";
  renderListMode(kind);
}

/** De in te lezen items van een tabblad: uit het lijst-tekstvak als dat
 *  actief is, anders uit de rijen. Dubbele invoer gaat eruit — op invoer
 *  én taal, want dezelfde regeling in twee talen zijn juist twee documenten. */
function readInput(kind) {
  const items = state.bulk[kind]
    ? parseList($(`#bulk-${kind}-text`).value).items.map((query) => ({
        query,
        lang: $(`#bulk-${kind}-lang`)?.value || "NL",
      }))
    : readRows(kind);
  const seen = new Set();
  return items.filter((item) => {
    const key = `${item.query.toLowerCase()}|${item.lang}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/* --------------------------------------------------------------------------
   Ophalen — met een kleine pool, één mislukking blokkeert de rest niet
   -------------------------------------------------------------------------- */

/**
 * Hoeveel documenten er tegelijk worden opgehaald.
 *
 * Bewust een kleine pool en niet alles tegelijk: een lijst van dertig links
 * zou dertig gelijktijdige verzoeken naar dezelfde bron sturen (EUR-Lex,
 * wetten.overheid.nl) en dat is precies hoe je throttling of een blokkade
 * uitlokt — nog voordat de eerste conversie klaar is.
 */
const BATCH_CONCURRENCY = 4;

/**
 * Verwerk een lijst taken met een kleine pool en rapporteer voortgang en
 * deelmislukkingen. Eén fout blokkeert de rest niet.
 * @param {Array} items       de op te halen dingen
 * @param {Function} label    item → naam voor de foutmelding
 * @param {Function} run      (item, index) → Promise die een document toevoegt
 * @param {string} noun       "opgehaald" / "geconverteerd"
 */
async function runBatch(items, label, run, noun) {
  let done = 0;
  const failures = [];
  const base = state.docs.length;
  const tick = () => setStatus(`Bezig: ${done}/${items.length} ${noun}…`, "info", { busy: true });
  tick();

  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next++;
      try {
        await run(items[index], index);
      } catch (e) {
        failures.push(`${label(items[index])} — ${e.message}`);
      } finally {
        done += 1;
        tick();
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(BATCH_CONCURRENCY, items.length) }, worker)
  );

  finishBatch(base);

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

/**
 * Zet de documenten van deze batch in de volgorde van de aangeleverde lijst
 * en open het eerste.
 *
 * De bronnen antwoorden in willekeurige volgorde, dus zonder deze stap staat
 * de tabbalk bij een lijst van twintig in een andere volgorde dan je lijst.
 * Openen gebeurt hier en niet in addDoc(), zodat de editor tijdens het
 * ophalen niet meespringt met elk document dat binnenkomt.
 */
function finishBatch(base) {
  const batch = state.docs.slice(base);
  if (!batch.length) return;
  batch.sort((a, b) => a.batchIndex - b.batchIndex);
  state.docs.length = base;
  state.docs.push(...batch);
  setActive(batch[0].id);
}

function withBusyButton(button, work) {
  button.disabled = true;
  return work().finally(() => {
    button.disabled = false;
  });
}

async function fetchLinks(kind) {
  const items = readInput(kind);
  if (!items.length) {
    setStatus("Voer minstens één ECLI, CELEX of link in.", "err");
    return;
  }
  await withBusyButton($(`#fetch-${kind}`), () =>
    runBatch(
      items,
      (item) => item.query,
      async (item, index) => {
        const data = await postJSON("/api/convert/link", item);
        const name = deriveName(item.query);
        addDoc({
          title: name, filenameBase: name, ...data,
          batchIndex: index, activate: false,
        });
      },
      "opgehaald"
    )
  );
}

/* --------------------------------------------------------------------------
   Open overheid — ophalen en zoeken

   Eén endpoint (/api/convert/overheid) voor kamerstukken, Kamervragen,
   Handelingen, bijlagen en open overheid-documenten; de server beslist welke bron het is.
   Zoeken (/api/search) levert per resultaat de `query` die ophalen direct
   begrijpt, dus een zoekresultaat en een bijlage gaan door dezelfde functie.
   -------------------------------------------------------------------------- */

/** Wat er op dit moment voor een bijlage/resultaat wordt opgehaald (op `query`). */
const ooBusy = new Set();

async function fetchOverheidItems(items, button, { collapse = false } = {}) {
  const docsBefore = state.docs.length;
  const fresh = items.filter((it) => {
    // Staat dit document al open? Dan alleen ernaartoe, geen tweede tabblad.
    const open = state.docs.find((d) => d.ident && d.ident === it.query);
    if (open) setActive(open.id);
    return !open;
  });
  if (!fresh.length) {
    if (collapse) { oo.collapsed = true; renderResults(); }
    return;
  }
  fresh.forEach((it) => ooBusy.add(it.query));
  renderResults();
  const run = () =>
    runBatch(
      fresh,
      (item) => item.query,
      async (item, index) => {
        const data = await postJSON("/api/convert/overheid", { query: item.query });
        const name = data.name || data.ident
          || item.query.replace(/[^\w.-]+/g, "-").replace(/^-+|-+$/g, "") || "document";
        addDoc({
          title: name, filenameBase: name, ...data,
          batchIndex: index, activate: false,
        });
      },
      "opgehaald"
    );
  try {
    await (button ? withBusyButton(button, run) : run());
  } finally {
    fresh.forEach((it) => ooBusy.delete(it.query));
    // Opgehaald vanuit de zoeklijst? Dan klapt die in (hij blijft bestaan), zodat het
    // resultaat meteen in beeld is.
    if (collapse && state.docs.length > docsBefore) oo.collapsed = true;
    renderResults();
  }
}

async function fetchOverheid() {
  const items = readInput("oo");
  if (!items.length) {
    setStatus("Voer minstens één kamerstuk, open overheid-document, identifier of link in.", "err");
    return;
  }
  await fetchOverheidItems(items, $("#fetch-oo"));
}

/* -- Zoeken -------------------------------------------------------------- */

const oo = {
  mode: "fetch",            // "fetch" | "search" (alleen bij de subtab "Stukken")
  sub: "stukken",           // "stukken" | "consultaties" | "wgk"
  stukken: "alles",         // keuze binnen "Stukken": "alles" (beide samengevoegd) | "pub" (SRU) | "woo"
  filters: {},              // extra filters van de huidige bron (zie OO_FILTERS)
  q: "", soort: "", sort: "nieuwste", van: "", tot: "",
  start: 0, n: 20, total: 0,
  /** Hoe diep er te bladeren valt (bij "alles" begrensd), en de totalen per bron. */
  limit: 0, totals: null,
  /** De resultaatlijst is ingeklapt (na ophalen) — alleen de balk om hem weer te openen blijft. */
  collapsed: false,
  /** Gezet als de zoekterm een dossiernummer was: dan is het resultaat het hele dossier. */
  dossier: "",
  results: [], selected: new Set(),
  soorten: { pub: [], woo: [] },
  loading: false, searched: false,
  /** Verzoek-token: alleen het laatste antwoord mag de UI bijwerken. */
  token: 0,
};

/** De bron waarin gezocht wordt: bij "Stukken" de keuze in de lijst, anders de subtab. */
const ooScope = () =>
  oo.sub === "consultaties" ? "consultatie" : oo.sub === "wgk" ? "wgk" : oo.stukken;

/** Extra filters per bron (naast zoekterm, soort, sortering en datums). */
const OO_FILTERS = {
  consultatie: [
    { key: "zoekin", aria: "Zoeken in", options: [["", "Titel en tekst"], ["titel", "Alleen titel"]] },
  ],
  wgk: [
    { key: "status", aria: "Status", options: [["", "Alle statussen"], ["inwording", "In wording"],
      ["naderend", "Naderend"], ["beeindigd", "Beëindigd"]] },
    { key: "fase", aria: "Fase", options: [["", "Alle fasen"], ...["Voorbereiding", "Raad van State",
      "Tweede Kamer", "Eerste Kamer", "Bekendmaking"].map((f) => [f, f])] },
    { key: "type", aria: "Soort regeling", options: [["", "Wet of AMvB"], ["Wet", "Wet"], ["Amvb", "AMvB"]] },
  ],
};
const OO_PLACEHOLDER = {
  stukken: "Zoekterm, dossiernummer (36600-VII) of onderwerp",
  consultaties: "Zoekterm, bv. politie of Wet gegevensvergaring",
  wgk: "Zoekterm (naam van de wet of AMvB), leeg = alles",
};

const NL_DATE = new Intl.DateTimeFormat("nl-NL", { day: "numeric", month: "short", year: "numeric" });
function formatDate(iso) {
  const d = /^\d{4}-\d{2}-\d{2}/.test(iso || "") ? new Date(`${iso.slice(0, 10)}T00:00:00`) : null;
  return d && !Number.isNaN(d.getTime()) ? NL_DATE.format(d) : iso || "";
}

function renderOO() {
  const scope = ooScope();
  const stukken = oo.sub === "stukken";
  const search = !stukken || oo.mode === "search";
  $$(".subtab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.sub === oo.sub)));
  $("#oo-modes").hidden = !stukken;
  $("#oo-fetch").hidden = !(stukken && !search);
  $("#oo-search").hidden = !search;
  $("#mode-oo-fetch").setAttribute("aria-pressed", String(!search));
  $("#mode-oo-search").setAttribute("aria-pressed", String(search));
  $("#oo-hint-stukken").hidden = !stukken;
  $("#oo-hint-consultaties").hidden = oo.sub !== "consultaties";
  $("#oo-hint-wgk").hidden = oo.sub !== "wgk";
  $("#oo-q").placeholder = OO_PLACEHOLDER[oo.sub];

  $("#oo-scope").hidden = !stukken;
  $("#oo-scope").value = oo.stukken;
  $("#oo-sort").value = oo.sort;
  // Sortering en soort bestaan alleen waar de bron dat kent; wetgevingskalender kent geen datums.
  const hasSoort = scope === "pub" || scope === "woo";
  $("#oo-sort").hidden = !(stukken);
  $("#oo-dates-from").hidden = $("#oo-dates-to").hidden = scope === "wgk";

  const soort = $("#oo-soort");
  const options = scope === "woo"
    ? [{ key: "", label: "Alle soorten" }, ...oo.soorten.woo]
    : scope === "pub" ? oo.soorten.pub : [];
  soort.replaceChildren(
    ...options.map((o) => {
      const opt = document.createElement("option");
      opt.value = o.key;
      opt.textContent = o.label;
      return opt;
    })
  );
  soort.value = options.some((o) => o.key === oo.soort) ? oo.soort : options[0]?.key ?? "";
  oo.soort = hasSoort ? soort.value : "";
  soort.hidden = !hasSoort;

  // Extra, bron-specifieke filters.
  $("#oo-extra").replaceChildren(
    ...(OO_FILTERS[scope] || []).map((f) => {
      const select = document.createElement("select");
      select.className = "select";
      select.id = `oo-f-${f.key}`;
      select.setAttribute("aria-label", f.aria);
      f.options.forEach(([value, label]) => {
        const opt = document.createElement("option");
        opt.value = value;
        opt.textContent = label;
        select.appendChild(opt);
      });
      select.value = oo.filters[f.key] || "";
      select.addEventListener("change", () => { oo.filters[f.key] = select.value; });
      return select;
    })
  );
  $("#oo-go").disabled = oo.loading;
  renderResults();
}

function renderResults() {
  const list = $("#oo-results");
  const head = $("#oo-results-head");
  const pager = $("#oo-pager");
  head.hidden = !oo.results.length;
  pager.hidden = !oo.results.length;
  const toggle = $("#oo-toggle");
  toggle.hidden = !oo.results.length;
  toggle.setAttribute("aria-expanded", String(!oo.collapsed));
  $("#oo-toggle-text").textContent = oo.collapsed
    ? `Zoekresultaten tonen (${oo.total.toLocaleString("nl-NL")} ${oo.total === 1 ? "resultaat" : "resultaten"})`
    : "Zoekresultaten verbergen";
  $("#oo-resultbox").hidden = oo.collapsed && oo.results.length > 0;

  if (!oo.results.length) {
    const empty = document.createElement("li");
    empty.className = "result-empty";
    empty.textContent = oo.loading ? "Zoeken…"
      : oo.searched ? "Geen resultaten. Probeer een andere zoekterm of minder filters." : "";
    list.replaceChildren(...(empty.textContent ? [empty] : []));
    return;
  }

  const from = oo.start + 1;
  const to = oo.start + oo.results.length;
  const split = oo.totals
    ? ` (publicaties ${oo.totals.pub?.toLocaleString("nl-NL") ?? "–"} · Open overheid ${oo.totals.woo?.toLocaleString("nl-NL") ?? "–"})`
    : "";
  $("#oo-count").textContent = oo.dossier
    ? `Dossier ${oo.dossier} — ${oo.total.toLocaleString("nl-NL")} stukken, oudste eerst — ${from}–${to} getoond`
    : `${oo.total.toLocaleString("nl-NL")} ${oo.total === 1 ? "resultaat" : "resultaten"}${split} — ${from}–${to} getoond`;
  const allSelected = oo.results.every((r) => oo.selected.has(r.id));
  $("#oo-select-all").checked = allSelected;
  const nSel = oo.selected.size;
  const fetchSel = $("#oo-fetch-selected");
  fetchSel.disabled = nSel === 0;
  fetchSel.textContent = nSel ? `Geselecteerde ophalen (${nSel})` : "Geselecteerde ophalen";
  $("#oo-prev").disabled = oo.loading || oo.start === 0;
  const reach = oo.limit || oo.total;
  $("#oo-next").disabled = oo.loading || oo.start + oo.n >= reach;
  $("#oo-page").textContent =
    `Pagina ${Math.floor(oo.start / oo.n) + 1} van ${Math.max(1, Math.ceil(reach / oo.n))}`;

  list.replaceChildren(
    ...oo.results.map((r) => {
      const li = document.createElement("li");
      li.className = "result";

      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = oo.selected.has(r.id);
      check.setAttribute("aria-label", `Selecteer ${r.titel}`);
      check.addEventListener("change", () => {
        if (check.checked) oo.selected.add(r.id);
        else oo.selected.delete(r.id);
        renderResults();
      });

      const body = document.createElement("div");
      body.className = "result-body";
      const title = document.createElement("a");
      title.className = "result-title";
      title.href = r.open_url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
      title.textContent = r.titel;
      const meta = document.createElement("div");
      meta.className = "result-meta";
      // Bij "alles" staat het meteen bij elk resultaat uit welke bron het komt.
      if (ooScope() === "alles") {
        const bron = document.createElement("span");
        bron.className = `chip chip-${r.bronsoort}`;
        bron.textContent = r.bronsoort === "woo" ? "Open overheid" : "Officiële publicatie";
        meta.appendChild(bron);
        if (r.ook_woo) {
          const ook = document.createElement("span");
          ook.className = "chip chip-woo";
          ook.title = "Dit stuk staat ook bij open.overheid.nl";
          ook.textContent = "ook Open overheid";
          meta.appendChild(ook);
        }
      }
      if (r.soort) {
        const chip = document.createElement("span");
        chip.className = "chip";
        chip.textContent = r.soort;
        meta.appendChild(chip);
      }
      meta.append([formatDate(r.datum), r.bron, r.meta].filter(Boolean).join(" · "));
      body.append(title, meta);
      if (r.snippet) {
        const snippet = document.createElement("p");
        snippet.className = "result-snippet";
        snippet.textContent = r.snippet;
        body.appendChild(snippet);
      }

      const open = state.docs.some((d) => d.ident === r.query);
      const get = document.createElement("button");
      get.type = "button";
      get.className = "btn btn-outline btn-sm";
      get.textContent = ooBusy.has(r.query) ? "Bezig…" : open ? "Tonen" : "Ophalen";
      get.disabled = ooBusy.has(r.query);
      get.addEventListener("click", () => fetchOverheidItems([{ query: r.query }], get, { collapse: true }));

      li.append(check, body, get);
      return li;
    })
  );
}

async function runSearch(start = 0) {
  oo.q = $("#oo-q").value.trim();
  oo.van = $("#oo-van").value;
  oo.tot = $("#oo-tot").value;
  if (oo.van && oo.tot && oo.van > oo.tot) {
    setStatus("De begindatum ligt na de einddatum.", "err");
    return;
  }
  // Een nieuwe zoekopdracht begint weer met de gewone paginagrootte; een dossier-
  // antwoord zet 'm zo nodig hoger (en blijft dan gelden voor de volgende pagina's).
  if (start === 0) oo.n = 20;
  oo.start = start;
  oo.loading = true;
  oo.collapsed = false;            // een nieuwe zoekopdracht toont de lijst weer
  const token = ++oo.token;
  renderOO();
  const params = new URLSearchParams({
    scope: ooScope(), q: oo.q, soort: oo.soort, sort: oo.sort,
    van: oo.van, tot: oo.tot, start: String(start), n: String(oo.n),
  });
  Object.entries(oo.filters).forEach(([k, v]) => { if (v) params.set(k, v); });

  setStatus("Zoeken…", "info", { busy: true });
  try {
    const data = await api(`/api/search?${params}`);
    if (token !== oo.token) return;           // een nieuwere zoekopdracht is al onderweg
    oo.results = data.results;
    oo.total = data.total;
    oo.limit = data.limit ?? data.total;
    oo.totals = data.totals || null;
    oo.dossier = data.dossier || "";
    oo.n = data.n || oo.n;            // een dossier komt in grotere pagina's
    oo.searched = true;
    if (data.scope === "pub" || data.scope === "woo") oo.soorten[data.scope] = data.soorten.filter((o) => o.key !== "");
    // Eén bron die uitvalt mag de andere niet verbergen: tonen, maar wel melden.
    if (data.waarschuwing) setStatus(data.waarschuwing, "info");
    else clearStatus();
  } catch (e) {
    if (token !== oo.token) return;
    oo.results = [];
    oo.total = 0;
    oo.limit = 0;
    oo.totals = null;
    oo.dossier = "";
    oo.searched = false;
    setStatus(e.message, "err");
  } finally {
    if (token === oo.token) {
      oo.loading = false;
      renderOO();
      if (oo.results.length) $("#oo-results").scrollIntoView({ block: "nearest" });
    }
  }
}

async function initOpenOverheid() {
  // Vaste soorten voor parlementaire publicaties; soorten van open.overheid.nl komen uit de eerste zoekopdracht.
  try {
    oo.soorten.pub = (await api("/api/search/soorten")).soorten;
  } catch (_) { /* zonder lijst blijft "Zoeken" werken, alleen zonder soortfilter */ }
  oo.mode = localStorage.getItem("ooMode") === "search" ? "search" : "fetch";
  const savedSub = localStorage.getItem("ooSub");
  oo.sub = ["stukken", "consultaties", "wgk"].includes(savedSub) ? savedSub : "stukken";

  const setMode = (mode) => {
    oo.mode = mode;
    localStorage.setItem("ooMode", mode);
    renderOO();
    if (mode === "search") $("#oo-q").focus();
  };
  $("#mode-oo-fetch").addEventListener("click", () => setMode("fetch"));
  $("#mode-oo-search").addEventListener("click", () => setMode("search"));

  const resetResults = () => {
    oo.soort = "";
    oo.filters = {};
    oo.results = [];
    oo.total = 0;
    oo.dossier = "";
    oo.totals = null;
    oo.selected.clear();
    oo.searched = false;
    oo.collapsed = false;
  };
  $("#oo-scope").addEventListener("change", (e) => {
    oo.stukken = e.target.value;
    resetResults();
    renderOO();
  });
  $$(".subtab").forEach((tab) =>
    tab.addEventListener("click", () => {
      if (oo.sub === tab.dataset.sub) return;
      oo.sub = tab.dataset.sub;
      localStorage.setItem("ooSub", oo.sub);
      resetResults();
      renderOO();
      if (oo.sub !== "stukken") $("#oo-q").focus();
    })
  );
  $("#oo-soort").addEventListener("change", (e) => { oo.soort = e.target.value; });
  $("#oo-sort").addEventListener("change", (e) => { oo.sort = e.target.value; });
  $("#oo-form").addEventListener("submit", (e) => {
    e.preventDefault();
    oo.selected.clear();
    runSearch(0);
  });
  $("#oo-toggle").addEventListener("click", () => {
    oo.collapsed = !oo.collapsed;
    renderResults();
  });
  $("#oo-prev").addEventListener("click", () => runSearch(Math.max(0, oo.start - oo.n)));
  $("#oo-next").addEventListener("click", () => runSearch(oo.start + oo.n));
  $("#oo-select-all").addEventListener("change", (e) => {
    oo.results.forEach((r) => (e.target.checked ? oo.selected.add(r.id) : oo.selected.delete(r.id)));
    renderResults();
  });
  $("#oo-fetch-selected").addEventListener("click", async () => {
    const items = oo.results.filter((r) => oo.selected.has(r.id)).map((r) => ({ query: r.query }));
    oo.selected.clear();
    await fetchOverheidItems(items, $("#oo-fetch-selected"), { collapse: true });
  });
  renderOO();
}

/** Voor PDF's: losse ingesloten afbeeldingen (grafieken, screenshots) meenemen als bijlagen. */
function extractImagesRequested() {
  const el = $("#extract-images");
  return el && !el.closest("[hidden]") && el.checked;
}

/** Wiskunde-modus: PDF-pagina's door een vision-model transcriberen i.p.v. de
 *  snelle tekstextractie. Alleen zinvol met een OpenRouter-sleutel + poppler. */
function ocrModeRequested() {
  const el = $("#ocr-mode");
  return el && !el.closest("[hidden]") && el.checked;
}

async function fetchFileUrls() {
  const items = readInput("doc");
  if (!items.length) {
    setStatus("Plak minstens één link naar een bestand.", "err");
    return;
  }
  if (ocrModeRequested()) {
    await withBusyButton($("#fetch-doc"), () =>
      runOcr(items.map((it) => ({ url: it.query })), { viaUrl: true })
    );
    return;
  }
  const extractImages = extractImagesRequested() || undefined;
  await withBusyButton($("#fetch-doc"), () =>
    runBatch(
      items,
      (item) => item.query,
      async (item, index) => {
        const data = await postJSON("/api/convert/file-url", { url: item.query, extract_images: extractImages });
        const base = basename(item.query);
        addDoc({
          title: base, filenameBase: base, ...data, allowObsidian: true,
          batchIndex: index, activate: false,
        });
      },
      "opgehaald"
    )
  );
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  if (ocrModeRequested()) {
    await runOcr(files.map((file) => ({ file })), { viaUrl: false });
    return;
  }
  const extractImages = extractImagesRequested();
  await runBatch(
    files,
    (file) => file.name,
    async (file, index) => {
      const form = new FormData();
      form.append("file", file);
      if (extractImages) form.append("extract_images", "1");
      const data = await api("/api/convert/file", { method: "POST", body: form });
      const base = file.name.replace(/\.[^.]+$/, "") || "document";
      addDoc({
        title: base, filenameBase: base, ...data, allowObsidian: true,
        batchIndex: index, activate: false,
      });
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
        addDoc({ title: name, filenameBase: name, ...data, allowObsidian: true, activate: false });
        el.innerHTML = "";
      },
      "opgemaakt"
    )
  );
}

/** Een bestandsnaam voor de download, afgeleid uit de ingevoerde identifier. */
function deriveName(query) {
  const ecli = query.match(RE_ECLI);
  if (ecli) return ecli[0].replace(/:/g, "-");
  const bwb = query.match(RE_BWB);
  if (bwb) return bwb[0].toUpperCase();
  // CELEX vóór HUDOC: een geconsolideerde CELEX (02014R0910-20241018) matcht
  // óók het HUDOC-item-id-patroon, andersom kan niet.
  const celex = query.match(RE_CELEX);
  if (celex) return celex[0].toUpperCase();
  const hudoc = query.match(RE_HUDOC);
  return hudoc ? `HUDOC-${hudoc[0]}` : "document";
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
  // Met een lijst van twintig is per document downloaden het nieuwe handwerk.
  const all = $("#download-all");
  all.hidden = state.docs.length < 2;
  all.textContent = `Alles downloaden (${state.docs.length})`;
  all.title = `Alle ${state.docs.length} documenten in één .zip`;
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
  const waarschuwing = $("#doc-warnings");
  waarschuwing.hidden = doc.warnings.length === 0;
  waarschuwing.textContent = doc.warnings.length
    ? `Waarschuwing: ${doc.warnings.join(" · ")}`
    : "";
  updateLineNumbers();

  $("#download").textContent = doc.bundleToken
    ? "Download kennisbank .zip"
    : doc.attachmentCount
      ? `Download .zip (${doc.attachmentCount} afb.)`
      : "Download .md";

  if (!AI_ENABLED) return;

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
  const { label, usage, elapsedMs } = doc.lastUsage;
  const parts = [`${label}: ${fmt(usage.total_tokens)} tokens (${fmt(usage.prompt_tokens || 0)} invoer, ${fmt(usage.completion_tokens || 0)} uitvoer)`];
  if (usage.cost) parts.push(`${fmtCost(usage.cost)} (OpenRouter)`);
  if (elapsedMs) {
    const seconds = elapsedMs / 1000;
    const tokensPerSec = (usage.completion_tokens || 0) / seconds;
    parts.push(`${fmtDuration(seconds)} · ${tokensPerSec.toFixed(1)} uitvoertokens/s`);
  }
  el.textContent = parts.join(" · ");
  el.hidden = false;
}

/** Bv. "3,4s" of "1m 12s" — geen decimalen meer zodra het over een minuut loopt. */
function fmtDuration(seconds) {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
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
  const startedAt = performance.now();
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
    const elapsedMs = performance.now() - startedAt;
    doc.lastUsage = usage ? { label: resultLabel, usage, elapsedMs } : doc.lastUsage;
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
   Wiskunde-modus (OCR) — PDF pagina-voor-pagina door een vision-model

   Spiegelt runClean(): één streaming-aanroep per PDF, live in het tekstvak,
   annuleerbaar via dezelfde #cancel-clean-knop en activeCleans-Map. Het
   document wordt eerst leeg aangemaakt en loopt al streamend vol. Meerdere
   PDF's worden ná elkaar verwerkt (niet parallel) — elke PDF is al N
   vision-verzoeken, en gelijktijdig lopen verstoort de live-voortgang en lokt
   rate-limiting uit.
   -------------------------------------------------------------------------- */

async function runOcr(entries, { viaUrl }) {
  let done = 0;
  const failures = [];
  for (const entry of entries) {
    const name = viaUrl
      ? basename(entry.url)
      : entry.file.name.replace(/\.[^.]+$/, "") || "document";
    try {
      await streamOnePdf(entry, name, viaUrl);
    } catch (e) {
      if (e.name !== "AbortError") failures.push(`${name} — ${e.message}`);
    }
    done += 1;
    setStatus(`Bezig: ${done}/${entries.length} getranscribeerd…`, "info", { busy: true });
  }
  if (!failures.length) {
    setStatus(entries.length === 1 ? "Klaar." : `${entries.length} documenten getranscribeerd.`, "ok");
  } else {
    setStatus(
      `${entries.length - failures.length}/${entries.length} gelukt.`,
      "err",
      { detail: failures.join("  ·  ") }
    );
  }
}

async function streamOnePdf(entry, name, viaUrl) {
  const modelSelect = $("#ocr-model");
  const modelId = modelSelect.value || undefined;
  const modelLabel = modelSelect.selectedOptions[0]?.textContent || modelId || "OCR";
  const doc = addDoc({
    title: name,
    filenameBase: name,
    source: `wiskunde-OCR (${modelLabel}) • ${name}`,
    kind: "document",
    markdown: "",
    allowObsidian: true,
    activate: true,
  });

  const docId = doc.id;
  const isLive = () => state.activeId === docId;
  const startedAt = performance.now();
  const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const controller = new AbortController();
  activeCleans.set(docId, { requestId, controller });
  if (isLive()) renderEditor();
  setStatus(`"${name}" transcriberen met een vision-model…`, "info", { busy: true });

  try {
    let response;
    if (viaUrl) {
      response = await fetch("/api/convert/file-url/ocr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: entry.url, model: modelId, request_id: requestId }),
        signal: controller.signal,
      });
    } else {
      const form = new FormData();
      form.append("file", entry.file);
      if (modelId) form.append("model", modelId);
      form.append("request_id", requestId);
      response = await fetch("/api/convert/file/ocr", {
        method: "POST",
        body: form,
        signal: controller.signal,
      });
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Fout ${response.status}`);
    }

    if (isLive()) $("#md").value = "";
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let acc = "";
    let usage = null;
    const parser = makeStreamParser({
      onText: (t) => {
        acc += t;
        doc.markdown = acc;
        if (isLive()) {
          $("#md").value = acc;
          updateLineNumbers();
        }
      },
      onProgress: (p) => {
        if (isLive()) setProgress(p.produced_tokens, p.expected_tokens);
        setStatus(
          `"${name}": pagina ${p.produced_tokens}/${p.expected_tokens} getranscribeerd…`,
          "info",
          { busy: true }
        );
      },
      onUsage: (u) => { usage = u; },
    });
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    const err = parser.finish();
    if (err !== null) throw new Error(err || "Transcriptie mislukt.");

    doc.markdown = acc.trim() + "\n";
    doc.lastUsage = usage
      ? { label: "Wiskunde-OCR", usage, elapsedMs: performance.now() - startedAt }
      : null;
    renderDocTabs();
  } catch (e) {
    if (e.name === "AbortError") {
      // Wat er al binnen was blijft staan; niet als mislukking tellen.
      doc.markdown = doc.markdown.trim() ? doc.markdown.trim() + "\n" : "";
      setStatus(`"${name}" geannuleerd.`, "info");
    } else if (!doc.markdown.trim()) {
      // Niets bruikbaars binnengekomen: het lege tabblad weer opruimen.
      closeDoc(docId);
    }
    throw e;
  } finally {
    activeCleans.delete(docId);
    if (isLive()) renderEditor();
  }
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
  schedulePreview();
}

/* --------------------------------------------------------------------------
   Weergave naast de ruwe tekst (static/mdview.js)

   Eén Markdown-bron, twee gezichten: de textarea met regelnummers en de gerenderde
   weergave. Alleen wat zichtbaar is wordt gerenderd; tijdens een stream (opschonen)
   ververst de weergave hooguit om de 600 ms in plaats van bij elk stukje tekst.
   -------------------------------------------------------------------------- */

const VIEWS = ["raw", "split", "preview"];
const preview = { mode: "split", timer: null, last: 0, lock: 0 };

function renderView() {
  $("#viewer").dataset.view = preview.mode;
  VIEWS.forEach((v) => $(`#view-${v}`).setAttribute("aria-pressed", String(v === preview.mode)));
  schedulePreview(0);
  updateLineNumbers();           // de editor is misschien net zichtbaar/van breedte veranderd
}

function schedulePreview(delay = 150) {
  if (preview.mode === "raw" || typeof mdToHtml !== "function") return;
  clearTimeout(preview.timer);
  // Wachten tot het typen/streamen even stilvalt, maar nooit langer dan 600 ms oud.
  const wait = performance.now() - preview.last >= 600 ? 0 : delay;
  preview.timer = setTimeout(updatePreview, wait);
}

function updatePreview() {
  if (preview.mode === "raw") return;
  preview.last = performance.now();
  const doc = activeDoc();
  const token = doc && doc.attachmentsToken;
  const pane = $("#preview");
  const top = pane.scrollTop;
  pane.innerHTML = mdToHtml($("#md").value, {
    // Geëxtraheerde afbeeldingen (![[p01.png]]) staan onder het bijlage-token van het document.
    embedUrl: token ? (name) => `/api/attachments/${token}/${encodeURIComponent(name)}` : null,
  });
  pane.scrollTop = top;
}

function initPreview() {
  const saved = localStorage.getItem("mdView");
  preview.mode = VIEWS.includes(saved) ? saved : "split";
  VIEWS.forEach((v) =>
    $(`#view-${v}`).addEventListener("click", () => {
      preview.mode = v;
      localStorage.setItem("mdView", v);
      renderView();
    })
  );
  // Voetnoten en ankers scrollen binnen de weergave, niet de hele pagina.
  $("#preview").addEventListener("click", (e) => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    e.preventDefault();
    const target = $("#preview").querySelector(`[id="${a.getAttribute("href").slice(1)}"]`);
    if (target) target.scrollIntoView({ block: "nearest" });
  });
  // Naast elkaar: scrollen loopt evenredig mee (met een korte vergrendeling tegen echo's).
  const follow = (from, to) => () => {
    if (preview.mode !== "split" || performance.now() < preview.lock) return;
    const max = from.scrollHeight - from.clientHeight;
    if (max <= 0) return;
    preview.lock = performance.now() + 80;
    to.scrollTop = (from.scrollTop / max) * (to.scrollHeight - to.clientHeight);
  };
  $("#md").addEventListener("scroll", follow($("#md"), $("#preview")), { passive: true });
  $("#preview").addEventListener("scroll", follow($("#preview"), $("#md")), { passive: true });
  renderView();
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

/** Het antwoord van /api/download als bestand opslaan. */
async function saveDownload(body, filename) {
  const response = await fetch("/api/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    setStatus(data.error || "Downloaden is mislukt.", "err");
    return;
  }
  // De server beslist of het een zip (bijlagen, of een kennisbankbundel) of een los
  // .md wordt — een onbekende bijlagen-token, bv. na een herstart, geeft een .md —
  // dus de naam volgt het antwoord, niet wat de aanroeper verwachtte. Verwachtte de
  // aanroeper bijlagen en komt er toch een .md, dan hoort de gebruiker dat (Floris,
  // 31dc203): anders mist hij stil zijn afbeeldingen.
  const blob = await response.blob();
  const extension = blob.type === "application/zip" ? "zip" : "md";
  const basename = filename.replace(/\.(?:md|zip)$/i, "");
  if (/\.zip$/i.test(filename) && extension !== "zip" && body.attachments_token) {
    setStatus("De afbeeldingen zijn niet meer beschikbaar (de server is herstart of de sessie is verlopen); alleen de markdown is gedownload. Converteer het bestand opnieuw voor de afbeeldingen.", "err");
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${basename}.${extension}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
}

async function downloadActive() {
  const doc = activeDoc();
  if (!doc) return;
  saveEdits();
  // Zijn er losse afbeeldingen geëxtraheerd (Documentupload, PDF), dan bouwt
  // /api/download een .zip met de markdown + een attachments/-map i.p.v. een
  // los .md-bestand — zie mdconv/attachments.py.
  await saveDownload(
    {
      markdown: doc.markdown, filename: doc.filenameBase,
      attachments_token: doc.attachmentsToken || undefined,
      bundle_token: doc.bundleToken || undefined,
      bewerkt_met_ai: doc.cleaned || doc.translated,
    },
    `${doc.filenameBase}.${doc.attachmentsToken ? "zip" : "md"}`
  );
}

/** Alle opgehaalde documenten in één zip: `<naam>.md` per document, en de
 *  bijlagen per document onder `attachments/<naam>/`. */
async function downloadAll() {
  if (!state.docs.length) return;
  saveEdits();
  const name = `markdown-${new Date().toISOString().slice(0, 10)}`;
  await saveDownload(
    {
      filename: name,
      documents: state.docs.map((doc) => ({
        markdown: doc.markdown,
        filename: doc.filenameBase,
        attachments_token: doc.attachmentsToken || undefined,
        bundle_token: doc.bundleToken || undefined,
        bewerkt_met_ai: doc.cleaned || doc.translated,
      })),
    },
    `${name}.zip`
  );
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
    if (!AI_ENABLED) return;
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

    // Wiskunde-modus: alleen zinvol met een sleutel én poppler (pdftoppm).
    $("#ocr-opts").hidden = !(state.llmAvailable && cfg.ocr_available);
    const ocrSelect = $("#ocr-model");
    const prevOcr = ocrSelect.value;
    ocrSelect.replaceChildren(
      ...(cfg.ocr_models || []).map((m) => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = m.label || m.id;
        return opt;
      })
    );
    const rememberedOcr = localStorage.getItem("ocrModel");
    for (const candidate of [prevOcr, rememberedOcr]) {
      if (candidate && [...ocrSelect.options].some((o) => o.value === candidate)) {
        ocrSelect.value = candidate;
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

function modelRow(id = "", label = "", chunkTokens = null) {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <div class="field" style="flex:0 0 32%"><input type="text" class="mid" placeholder="model-id" aria-label="Model-id"></div>
    <div class="field"><input type="text" class="mlabel" placeholder="label in de lijst" aria-label="Label"></div>
    <div class="field" style="flex:0 0 110px">
      <input type="number" class="mchunk" step="1000" placeholder="standaard" aria-label="Tokens per deel voor dit endpoint">
    </div>
    <button type="button" class="btn btn-ghost btn-icon btn-danger" aria-label="Verwijderen">✕</button>`;
  row.querySelector(".mid").value = id;
  row.querySelector(".mlabel").value = label;
  const chunkInput = row.querySelector(".mchunk");
  chunkInput.value = chunkTokens || "";
  if (state.settings) {
    chunkInput.min = state.settings.defaults.min_chunk_tokens;
    chunkInput.max = state.settings.defaults.max_chunk_tokens;
  }
  row.querySelector("button").addEventListener("click", () => row.remove());
  return row;
}

/** Rij voor een wiskunde-OCR-model: alleen id + label (geen deelgrootte). */
function ocrModelRow(id = "", label = "") {
  const row = document.createElement("div");
  row.className = "row";
  row.innerHTML = `
    <div class="field" style="flex:0 0 40%"><input type="text" class="omid" placeholder="model-id" aria-label="Model-id"></div>
    <div class="field"><input type="text" class="omlabel" placeholder="label in de lijst" aria-label="Label"></div>
    <button type="button" class="btn btn-ghost btn-icon btn-danger" aria-label="Verwijderen">✕</button>`;
  row.querySelector(".omid").value = id;
  row.querySelector(".omlabel").value = label;
  row.querySelector("button").addEventListener("click", () => row.remove());
  return row;
}

function renderOcrModelRows(models) {
  $("#settings-ocr-models").replaceChildren(
    ...(models || []).map((m) => ocrModelRow(m.id, m.label))
  );
}

function renderModelRows(models) {
  $("#settings-models").replaceChildren(
    ...(models || []).map((m) => modelRow(m.id, m.label, m.chunk_tokens))
  );
}

async function openSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (e) {
    setStatus(e.message, "err");
    return;
  }
  const s = state.settings;
  $("#settings-ai").checked = s.ai_enabled;
  $("#settings-msg").textContent = "";
  if (AI_ENABLED) fillAiSettings(s);

  dialog.lastFocus = document.activeElement;
  $("#settings").classList.add("show");
  document.body.style.overflow = "hidden";
  $("#settings-close").focus();
}

/** De AI-velden van het paneel — alleen aanwezig als AI aan staat. */
function fillAiSettings(s) {
  renderModelRows(s.models);
  renderOcrModelRows(s.ocr_models);
  // Leeg tonen als het de standaardwaarde is (zelfde "leeg = standaard"-idee
  // als de deelgrootte per endpoint).
  $("#settings-ocr-pages").value =
    s.ocr_pages_per_request === s.defaults.ocr_pages_per_request ? "" : s.ocr_pages_per_request;
  $("#settings-chunk-default").textContent = fmt(s.defaults.chunk_tokens);
  $("#settings-chunk-range").textContent =
    `${fmt(s.defaults.min_chunk_tokens)}–${fmt(s.defaults.max_chunk_tokens)}`;
  $("#prompt-generic").value = s.prompts.generic;
  $("#prompt-caselaw").value = s.prompts.caselaw;
  $("#prompt-obsidian").value = s.prompts.obsidian;
  $("#prompt-translate_nl").value = s.prompts.translate_nl;
  $("#prompt-ocr").value = s.ocr_prompt;
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
  const aiOn = $("#settings-ai").checked;
  // De AI-onderdelen staan server-side wel of niet in de pagina (index.html),
  // dus omschakelen = herladen. Opgehaalde documenten leven alleen in de
  // browser en zouden dan verdwijnen: eerst vragen.
  const reload = aiOn !== AI_ENABLED;
  if (reload && state.docs.length &&
      !confirm("De pagina wordt opnieuw geladen om AI aan of uit te zetten. "
        + "Opgehaalde documenten die je niet hebt gedownload, gaan verloren. Doorgaan?")) {
    return;
  }

  const button = $("#settings-save");
  const msg = $("#settings-msg");
  button.disabled = true;
  try {
    // Bij omschakelen alleen de schakelaar: de AI-velden meesturen zou de
    // huidige standaardwaarden vastleggen in settings.json.
    const aiFields = AI_ENABLED && !reload ? readAiSettings() : {};
    await postJSON("/api/settings", { ai_enabled: aiOn, ...aiFields });
    if (reload) {
      location.reload();
      return;
    }
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

/** De AI-velden van het paneel als payload voor /api/settings. */
function readAiSettings() {
  const models = $$("#settings-models .row").map((row) => ({
    id: row.querySelector(".mid").value.trim(),
    label: row.querySelector(".mlabel").value.trim(),
    chunk_tokens: parseInt(row.querySelector(".mchunk").value, 10) || null,
  })).filter((m) => m.id);

  const ocrModels = $$("#settings-ocr-models .row").map((row) => ({
    id: row.querySelector(".omid").value.trim(),
    label: row.querySelector(".omlabel").value.trim(),
  })).filter((m) => m.id);
  const ocrPages = parseInt($("#settings-ocr-pages").value, 10) || null;

  return {
    models,
    ocr_models: ocrModels,
    ocr_pages_per_request: ocrPages,
    ocr_prompt: $("#prompt-ocr").value,
    prompts: {
      generic: $("#prompt-generic").value,
      caselaw: $("#prompt-caselaw").value,
      obsidian: $("#prompt-obsidian").value,
      translate_nl: $("#prompt-translate_nl").value,
    },
  };
}

function initSettings() {
  $("#open-settings").addEventListener("click", openSettings);
  $("#settings-close").addEventListener("click", closeSettings);
  $("#settings-cancel").addEventListener("click", closeSettings);
  $("#settings-save").addEventListener("click", saveSettings);

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

  // Staat AI uit, dan bevat het paneel alleen de schakelaar "AI-functies".
  if (!AI_ENABLED) return;

  $("#settings-add-model").addEventListener("click", () => {
    $("#settings-models").appendChild(modelRow()).querySelector("input").focus();
  });
  $("#settings-add-ocr-model").addEventListener("click", () => {
    $("#settings-ocr-models").appendChild(ocrModelRow()).querySelector("input").focus();
  });

  // Per veld terug naar de ingebouwde standaardwaarde — puur client-side, want
  // die standaarden zitten al in het antwoord van /api/settings.
  const resets = {
    "reset-models": () => renderModelRows(state.settings.defaults.models),
    "reset-generic": () => { $("#prompt-generic").value = state.settings.defaults.prompts.generic; },
    "reset-caselaw": () => { $("#prompt-caselaw").value = state.settings.defaults.prompts.caselaw; },
    "reset-obsidian": () => { $("#prompt-obsidian").value = state.settings.defaults.prompts.obsidian; },
    "reset-translate_nl": () => {
      $("#prompt-translate_nl").value = state.settings.defaults.prompts.translate_nl;
    },
    "reset-ocr-models": () => renderOcrModelRows(state.settings.defaults.ocr_models),
    "reset-ocr": () => { $("#prompt-ocr").value = state.settings.defaults.ocr_prompt; },
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
   Glas: specular highlight die de cursor volgt + een zwevende kop

   Apple's Liquid Glass vangt licht dat verschuift met de kijkhoek — op een
   Mac is er geen kijkhoek, maar de cursor is de dichtstbijzijnde analogie.
   `--mx`/`--my` (in app.css als `@property` geregistreerd, dus animeerbaar)
   staan op elk `.glass`-element; `.glass::before` tekent daar een radiale
   highlight op. Zonder deze listener (aanraakscherm, toetsenbord, reduced
   motion) blijft de CSS-fallbackpositie gewoon staan — dit is verrijking,
   geen vereiste.
   -------------------------------------------------------------------------- */

function initGlassSpecular() {
  if (!window.matchMedia("(hover: hover) and (pointer: fine)").matches) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.querySelectorAll(".glass").forEach((el) => {
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      el.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
      el.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
    });
    el.addEventListener("pointerleave", () => {
      el.style.removeProperty("--mx");
      el.style.removeProperty("--my");
    });
  });
}

/** De kop is `position: sticky` en krijgt iets meer diepte (schaduw) zodra
 * er onder hem doorgescrold wordt — het "zweeft over de inhoud"-gevoel van
 * een Liquid Glass-navigatiebalk, i.p.v. een kop die gewoon met de pagina
 * meescrolt. */
function initHeaderElevation() {
  const header = $(".app-header");
  if (!header) return;
  const update = () => header.classList.toggle("is-scrolled", window.scrollY > 4);
  update();
  window.addEventListener("scroll", update, { passive: true });
}

/* --------------------------------------------------------------------------
   Opstarten
   -------------------------------------------------------------------------- */

function init() {
  initTabs();
  initEditor();
  if (SETTINGS_ENABLED) initSettings();
  if (AI_ENABLED) initCleanControls();
  initUpload();
  initPreview();
  initGlassSpecular();
  initHeaderElevation();

  initRows("jur", () => fetchLinks("jur"));
  initRows("wet", () => fetchLinks("wet"));
  initRows("oo", fetchOverheid);
  initRows("doc", fetchFileUrls);
  // Ná initRows: het lijst-tekstvak deelt de submit-handler van de rijen.
  initListMode("jur");
  initListMode("wet");
  initListMode("oo");
  initListMode("doc");

  $("#fetch-jur").addEventListener("click", () => fetchLinks("jur"));
  $("#fetch-wet").addEventListener("click", () => fetchLinks("wet"));
  $("#fetch-oo").addEventListener("click", fetchOverheid);
  initOpenOverheid();
  $("#fetch-doc").addEventListener("click", fetchFileUrls);
  $("#fetch-tekst").addEventListener("click", fetchPastedText);
  $("#paste-clipboard").addEventListener("click", pasteFromClipboard);
  $("#clear-tekst").addEventListener("click", () => {
    $("#paste-area").innerHTML = "";
    $("#paste-area").focus();
  });

  $("#copy").addEventListener("click", copyActive);
  $("#download").addEventListener("click", downloadActive);
  $("#download-all").addEventListener("click", downloadAll);

  loadConfig();
  renderDocTabs();
  renderEditor();
}

/** Opschoonpaneel en wiskunde-modus — alleen als AI aan staat. */
function initCleanControls() {
  $("#clean").addEventListener("click", cleanActiveDoc);
  $("#translate-nl").addEventListener("click", translateActiveDoc);
  $("#cancel-clean").addEventListener("click", cancelActiveClean);

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

  $("#ocr-mode").addEventListener("change", () => {
    $("#ocr-model").hidden = !$("#ocr-mode").checked;
  });
  $("#ocr-model").addEventListener("change", () => {
    localStorage.setItem("ocrModel", $("#ocr-model").value);
  });
}

document.addEventListener("DOMContentLoaded", init);

/* Kleine Markdown → HTML-renderer voor de weergave naast de ruwe tekst.

   Bewust zelf geschreven en zonder dependency: de tool heeft geen build-stap en doet
   geen externe verzoeken. Hij dekt wat de converters zelf produceren plus wat je
   normaal zelf typt: koppen, alinea's, lijsten (genest), tabellen, citaten, codeblokken,
   voetnoten ([^1]), links, afbeeldingen, Obsidian-embeds (![[x.png]]) en -wikilinks, en
   nadruk. Alles wordt eerst ge-escaped; alleen <br>, <sup> en <sub> blijven staan.

   Gebruik: mdToHtml(markdown, { embedUrl: (naam) => url | null }) → HTML-string. */
(function (root) {
  "use strict";

  const esc = (s) =>
    String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

  /** Alleen http(s), mailto, ankers en relatieve adressen; nooit javascript:/data:. */
  function safeUrl(url) {
    const u = String(url).trim();
    if (/^(https?:|mailto:|#|\/|\.\/|\.\.\/)/i.test(u) || !/^[a-z][a-z0-9+.-]*:/i.test(u)) return u;
    return "#";
  }

  /* ---------------------------------------------------------------- inline */

  function inline(text, ctx) {
    const held = [];
    const hold = (html) => `\u0000${held.push(html) - 1}\u0000`;
    let s = String(text);

    // Backslash-escapes en code-spans eerst: daarbinnen geldt geen opmaak.
    s = s.replace(/\\([\\`*_{}\[\]()#+\-.!|<>~$])/g, (_, c) => hold(esc(c)));
    s = s.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, (_, _t, code) => hold(`<code>${esc(code.trim())}</code>`));

    // Toegestane inline-HTML.
    s = s.replace(/<(br)\s*\/?>/gi, () => hold("<br>"));
    s = s.replace(/<(\/?)(sup|sub)>/gi, (_, slash, tag) => hold(`<${slash}${tag.toLowerCase()}>`));

    // Voetnootverwijzing [^id].
    s = s.replace(/\[\^([^\]\s]+)\]/g, (m, id) => {
      if (!ctx.footnotes.has(id)) return m;
      if (!ctx.order.includes(id)) ctx.order.push(id);
      const n = ctx.order.indexOf(id) + 1;
      return hold(`<sup class="fnref"><a href="#fn-${esc(id)}" id="fnref-${esc(id)}">${n}</a></sup>`);
    });

    // Obsidian: ![[bestand|alt]] (embed) en [[doel|tekst]] (wikilink).
    s = s.replace(/!\[\[([^\]|]+)(?:\|([^\]]*))?\]\]/g, (_, name, alt) => {
      const url = ctx.embedUrl ? ctx.embedUrl(name.trim()) : null;
      return hold(url
        ? `<img src="${esc(url)}" alt="${esc(alt || name)}" loading="lazy">`
        : `<span class="embed">▣ ${esc(name)}</span>`);
    });
    s = s.replace(/\[\[([^\]|]+)(?:\|([^\]]*))?\]\]/g, (_, target, label) =>
      hold(`<span class="wikilink">${esc(label || target)}</span>`));

    // Afbeeldingen en links: ![alt](url "titel"), [tekst](url "titel"), <https://…>.
    s = s.replace(/!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"([^"]*)")?\s*\)/g, (_, alt, url, title) =>
      hold(`<img src="${esc(safeUrl(url))}" alt="${esc(alt)}"${title ? ` title="${esc(title)}"` : ""} loading="lazy">`));
    for (let i = 0; i < 3; i++) {                       // een paar niveaus: link in link-tekst komt niet voor
      s = s.replace(/\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*(<[^>]*>|[^)\s]*(?:\([^)\s]*\)[^)\s]*)*)(?:\s+"([^"]*)")?\s*\)/g,
        (m, label, url, title) => {
          url = url.replace(/^<|>$/g, "");
          const href = safeUrl(url);
          const ext = /^https?:/i.test(href);
          return hold(`<a href="${esc(href)}"${title ? ` title="${esc(title)}"` : ""}${ext ? ' target="_blank" rel="noopener noreferrer"' : ""}>${inline(label, ctx)}</a>`);
        });
    }
    s = s.replace(/<((?:https?:\/\/|mailto:)[^>\s]+)>/gi, (_, url) =>
      hold(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`));
    s = s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<>()]+[^\s<>().,;:!?'"])/g, (_, pre, url) =>
      pre + hold(`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`));

    s = esc(s);

    // Nadruk. Volgorde: sterkste eerst; `_` alleen op woordgrenzen (snake_case blijft heel).
    s = s.replace(/\*\*\*(?=\S)([\s\S]*?\S)\*\*\*/g, "<strong><em>$1</em></strong>");
    s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*(?=[^\s*])([\s\S]*?[^\s*])\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>");
    s = s.replace(/(^|[^\w])_(?=[^\s_])([\s\S]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");

    // Harde regeleinden: twee spaties of een backslash aan het eind van een regel.
    s = s.replace(/(?: {2,}|\\)\n/g, "<br>\n");
    return s.replace(/\u0000(\d+)\u0000/g, (_, i) => held[+i]);
  }

  /* ----------------------------------------------------------------- blocks */

  const RE = {
    fence: /^ {0,3}(`{3,}|~{3,})\s*([\w+-]*)\s*$/,
    heading: /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/,
    hr: /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/,
    quote: /^ {0,3}>\s?/,
    ul: /^( {0,3})([-*+])\s+/,
    ol: /^( {0,3})(\d{1,9})([.)])\s+/,
    tableSep: /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/,
    footnoteDef: /^\[\^([^\]\s]+)\]:\s?(.*)$/,
  };

  const splitRow = (line) => {
    let t = line.trim();
    if (t.startsWith("|")) t = t.slice(1);
    if (t.endsWith("|") && !t.endsWith("\\|")) t = t.slice(0, -1);
    const cells = [];
    let cur = "";
    for (let i = 0; i < t.length; i++) {
      if (t[i] === "\\" && t[i + 1] === "|") { cur += "|"; i++; }
      else if (t[i] === "|") { cells.push(cur.trim()); cur = ""; }
      else cur += t[i];
    }
    cells.push(cur.trim());
    return cells;
  };

  const isBlank = (l) => /^\s*$/.test(l);
  const indentOf = (l) => l.match(/^ */)[0].length;
  const startsBlock = (l) =>
    RE.fence.test(l) || RE.heading.test(l) || RE.hr.test(l) || RE.quote.test(l) ||
    RE.ul.test(l) || RE.ol.test(l);

  function blocks(lines, ctx, depth = 0) {
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (isBlank(line)) { i++; continue; }

      let m;
      if ((m = line.match(RE.fence))) {                       // ``` codeblok
        const [, fence, lang] = m;
        const body = [];
        i++;
        while (i < lines.length && !new RegExp(`^ {0,3}${fence[0]}{${fence.length},}\\s*$`).test(lines[i])) body.push(lines[i++]);
        i++;
        out.push(`<pre><code${lang ? ` class="lang-${esc(lang)}"` : ""}>${esc(body.join("\n"))}</code></pre>`);
      } else if ((m = line.match(RE.heading))) {
        out.push(`<h${m[1].length}>${inline(m[2], ctx)}</h${m[1].length}>`);
        i++;
      } else if (RE.hr.test(line) && !RE.ul.test(line)) {
        out.push("<hr>");
        i++;
      } else if (RE.hr.test(line)) {                          // "- - -" is een lijn, geen lijst
        out.push("<hr>");
        i++;
      } else if (RE.quote.test(line)) {
        const inner = [];
        while (i < lines.length && (RE.quote.test(lines[i]) || (!isBlank(lines[i]) && !startsBlock(lines[i])))) {
          inner.push(lines[i].replace(RE.quote, ""));
          i++;
        }
        out.push(`<blockquote>${blocks(inner, ctx, depth + 1)}</blockquote>`);
      } else if (RE.ul.test(line) || RE.ol.test(line)) {
        i = list(lines, i, ctx, out, depth);
      } else if (i + 1 < lines.length && line.includes("|") && RE.tableSep.test(lines[i + 1]) && lines[i + 1].includes("-")) {
        const head = splitRow(line);
        const align = splitRow(lines[i + 1]).map((c) =>
          /^:-+:$/.test(c) ? "center" : /^-+:$/.test(c) ? "right" : /^:-+$/.test(c) ? "left" : "");
        i += 2;
        const rows = [];
        while (i < lines.length && !isBlank(lines[i]) && lines[i].includes("|")) rows.push(splitRow(lines[i++]));
        const cell = (tag, c, k) =>
          `<${tag}${align[k] ? ` style="text-align:${align[k]}"` : ""}>${inline(c, ctx)}</${tag}>`;
        out.push(
          `<div class="table-wrap"><table><thead><tr>${head.map((c, k) => cell("th", c, k)).join("")}</tr></thead>` +
          `<tbody>${rows.map((r) => `<tr>${head.map((_, k) => cell("td", r[k] ?? "", k)).join("")}</tr>`).join("")}</tbody></table></div>`
        );
      } else {                                                // alinea
        const para = [line];
        i++;
        while (i < lines.length && !isBlank(lines[i]) && !startsBlock(lines[i]) &&
               !(lines[i].includes("|") && i + 1 < lines.length && RE.tableSep.test(lines[i + 1]) && lines[i + 1].includes("-"))) {
          para.push(lines[i++]);
        }
        out.push(`<p>${inline(para.join("\n").replace(/^\s+/gm, ""), ctx)}</p>`);
      }
    }
    return out.join("\n");
  }

  /** Eén lijst (en alles wat erin genest zit) vanaf regel `i`; geeft de volgende regel terug. */
  function list(lines, i, ctx, out, depth) {
    const first = lines[i];
    const ordered = RE.ol.test(first);
    const marker = ordered ? first.match(RE.ol) : first.match(RE.ul);
    const start = ordered ? parseInt(marker[2], 10) : null;
    const same = ordered ? RE.ol : RE.ul;
    const items = [];
    let loose = false;

    while (i < lines.length) {
      const mm = lines[i].match(same);
      if (!mm) break;
      const width = mm[0].length;
      const body = [lines[i].slice(width)];
      i++;
      while (i < lines.length) {
        const l = lines[i];
        if (isBlank(l)) {
          // Een lege regel hoort bij het item als daarna iets ingesprongen volgt.
          let j = i;
          while (j < lines.length && isBlank(lines[j])) j++;
          if (j < lines.length && indentOf(lines[j]) >= width) { body.push(""); i++; loose = loose || j - i >= 0; continue; }
          break;
        }
        if (indentOf(l) >= width) { body.push(l.slice(width)); i++; continue; }
        if (!startsBlock(l) && !isBlank(body[body.length - 1])) { body.push(l.replace(/^\s+/, "")); i++; continue; }   // luie voortzetting
        break;
      }
      items.push(body);
      // Blanco regel(s) tussen twee items maken de lijst "los".
      let j = i;
      while (j < lines.length && isBlank(lines[j])) j++;
      if (j > i && j < lines.length && same.test(lines[j])) { loose = true; i = j; }
      else if (j > i) { break; }
    }

    const tag = ordered ? "ol" : "ul";
    const html = items.map((body) => {
      const inner = blocks(body, ctx, depth + 1);
      // Strakke lijst: een item met alleen een alinea toont die zonder <p>.
      // Een item met een lijst eronder (genest) telt ook als strak: de enige <p> is dan de kop.
      const tight = !loose && inner.startsWith("<p>") && (inner.match(/<p>/g) || []).length === 1;
      return `<li>${tight ? inner.replace(/^<p>([\s\S]*?)<\/p>/, "$1") : inner}</li>`;
    }).join("\n");
    out.push(`<${tag}${ordered && start !== 1 ? ` start="${start}"` : ""}>\n${html}\n</${tag}>`);
    return i;
  }

  /* ----------------------------------------------------------------- publiek */

  function mdToHtml(markdown, options = {}) {
    const ctx = { footnotes: new Map(), order: [], embedUrl: options.embedUrl || null };
    // Voetnootdefinities eruit halen (met ingesprongen vervolgregels).
    const src = String(markdown).replace(/\r\n?/g, "\n").split("\n");
    const body = [];
    for (let i = 0; i < src.length; i++) {
      const m = src[i].match(RE.footnoteDef);
      if (!m) { body.push(src[i]); continue; }
      const text = [m[2]];
      while (i + 1 < src.length && (/^( {2,}|\t)/.test(src[i + 1]) || (isBlank(src[i + 1]) && /^( {2,}|\t)/.test(src[i + 2] || "")))) {
        text.push(src[++i].replace(/^( {2,4}|\t)/, ""));
      }
      ctx.footnotes.set(m[1], text.join("\n"));
    }
    let html = blocks(body, ctx);
    if (ctx.order.length) {
      const items = ctx.order.map((id) => {
        const note = blocks(ctx.footnotes.get(id).split("\n"), ctx).replace(/<\/p>$/, "") +
          ` <a href="#fnref-${esc(id)}" class="fnback" aria-label="Terug naar de tekst">↩</a></p>`;
        return `<li id="fn-${esc(id)}">${note.startsWith("<p>") ? note : `<p>${note}</p>`}</li>`;
      });
      html += `\n<section class="footnotes"><hr><ol>\n${items.join("\n")}\n</ol></section>`;
    }
    return html;
  }

  root.mdToHtml = mdToHtml;
  if (typeof module !== "undefined" && module.exports) module.exports = { mdToHtml };
})(typeof window !== "undefined" ? window : globalThis);

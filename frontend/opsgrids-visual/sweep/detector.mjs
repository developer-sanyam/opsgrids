/**
 * The in-page half of the layout sweep. `detect` is serialised by Playwright and run inside the
 * page, so it must stay self-contained: no imports, no closure over module scope.
 *
 * It returns plain hit records, one per defect found at the current viewport:
 *   { cat, kind, sig, text, rects: [{x,y,w,h}], detail }
 *
 * Categories (the owner's defect class):
 *   a  horizontal page overflow
 *   b  an element whose box escapes its nearest bordered / shadowed ancestor
 *   c  siblings in one flex/grid row whose control boxes do not share a top, bottom or centre
 *   d  form-field row whose inputs sit at different heights (a label wrapped)
 *   e  text clipped, overflowing its own box, or overlapping other text (including chart ticks)
 *   i  informational: text deliberately truncated (ellipsis / line-clamp) with no title to read it in full
 *   f  the last content is within 16px of the bottom edge when scrolled to the end
 *
 * Mobile categories (`detectMobile`, `detectOverlay`, `focusObscured`; run below 768px):
 *   g  an interactive target under 44x44 CSS px (a checkbox counts with its label; a ::before/::after hit
 *      area counts; an inline link inside running prose is exempt)
 *   h  a text field whose computed font-size is under 16px (iOS zooms the page on focus)
 *   j  an open dialog / drawer / menu wider or taller than the viewport, or whose primary action is off-screen
 *      or clipped; a full-bleed sheet that ignores the safe-area insets
 *   k  a table wider than the viewport with no scroll container of its own (and no card layout instead)
 *   l  a fixed or sticky bar that covers content or a focused field, a fixed bar on a screen edge with no
 *      `env(safe-area-inset-*)` rule, a viewport meta without `viewport-fit=cover`
 *   m  text under 12px (SVG text measured after its viewBox scale), body font under 14px / table-cell text under 13px
 *
 * Every hit carries `owner`: `{ shared, component, file }`. It comes from React's dev-build creation stack on
 * the element's fiber, so it names the component that *rendered* the element, not an ancestor that merely
 * contains it. `shared` is true for `opsgrids-ui` and `opsgrids-client`; everything else is app-local.
 */

/**
 * Installs the helpers every in-page function shares as `window.__sweep`. Call it once after each
 * navigation, before `detect` and the others (page evaluations are separate, self-contained
 * functions, so a helper cannot simply be imported).
 */
export function installHelpers() {
  const FRAME = /at (?:\S+ )?\(?(https?:\/\/[^\s)]+?)(?:\?[^\s):]*)?:(\d+):(\d+)\)?\s*$/;
  const fileOf = (stack) => {
    const lines = (stack || "").split("\n").slice(2);
    for (const ln of lines) {
      const m = FRAME.exec(ln.trim());
      if (m) return m[1];
    }
    return null;
  };
  const ownerOf = (start) => {
    let el = start;
    let f = null;
    for (let i = 0; el && i < 6 && !f; i += 1, el = el.parentElement) {
      const key = Object.keys(el).find((k) => k.startsWith("__reactFiber$"));
      if (key) f = el[key];
    }
    for (let i = 0; f && i < 16; i += 1, f = f._debugOwner) {
      const url = fileOf(f._debugStack && f._debugStack.stack);
      if (!url || /node_modules|\.vite\/deps/.test(url)) continue;
      const m = /\/(opsgrids-ui|opsgrids-client)\/src\/(?:[^?]*\/)?([^/?]+)\.tsx?$/.exec(url);
      if (m) return { shared: true, component: (m[1] === "opsgrids-client" ? "client/" : "") + m[2], file: url.replace(/^.*\/frontend\//, "") };
      return { shared: false, component: "app-local", file: url.replace(/^https?:\/\/[^/]+/, "").replace(/^.*\/frontend\//, "") };
    }
    return { shared: false, component: "app-local", file: "" };
  };
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height };
  };
  const isSrOnly = (el) => {
    const r = el.getBoundingClientRect();
    return r.width <= 1 && r.height <= 1;
  };
  const inClosedDetails = (el) => {
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      if (n.tagName === "DETAILS" && !n.open && !(el.closest("summary") && el.closest("summary").parentElement === n)) return true;
    }
    return false;
  };
  const isVisible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || s.visibility === "collapse" || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !inClosedDetails(el);
  };
  const sigOf = (el) => {
    const parts = [];
    let n = el;
    for (let i = 0; n && n !== document.body && i < 3; i += 1, n = n.parentElement) {
      const cls = (typeof n.className === "string" ? n.className : "")
        .split(/\s+/)
        .filter((c) => c && !/^(?:sm|md|lg|xl|hover|focus|dark):/.test(c) && !/\[/.test(c))
        .slice(0, 3)
        .join(".");
      parts.push(n.tagName.toLowerCase() + (cls ? "." + cls : ""));
    }
    return parts.join(" < ");
  };
  const textOf = (el) => (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
  /** What the person can see of `el`: its box intersected with every clipping ancestor. */
  const visibleBox = (el) => {
    const r = el.getBoundingClientRect();
    let l = r.left, t = r.top, rt = r.right, b = r.bottom;
    for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.overflowX !== "visible" || s.overflowY !== "visible") {
        const c = n.getBoundingClientRect();
        if (s.overflowX !== "visible") { l = Math.max(l, c.left); rt = Math.min(rt, c.right); }
        if (s.overflowY !== "visible") { t = Math.max(t, c.top); b = Math.min(b, c.bottom); }
      }
    }
    return { left: l, top: t, right: rt, bottom: b, width: rt - l, height: b - t };
  };
  /** Style rules that read `env(safe-area-inset-*)`, with the edge they read. */
  const safeRules = [];
  const walk = (rules) => {
    for (const rule of Array.from(rules)) {
      if (rule.selectorText && /safe-area-inset/.test(rule.cssText)) {
        for (const edge of ["top", "bottom", "left", "right"]) if (rule.cssText.includes("safe-area-inset-" + edge)) safeRules.push({ selector: rule.selectorText, edge });
      }
      if (rule.cssRules && rule.cssRules.length > 0) walk(rule.cssRules);
    }
  };
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      walk(sheet.cssRules);
    } catch {
      /* a cross-origin sheet */
    }
  }
  /** The element, or something inside it, reads the safe-area inset for `edge`. */
  const safeAreaAware = (el, edge) => {
    const rules = safeRules.filter((r) => r.edge === edge);
    const matches = (node) => rules.some((r) => { try { return node.matches(r.selector); } catch { return false; } });
    if (matches(el)) return true;
    for (const d of el.querySelectorAll("*")) if (matches(d)) return true;
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) if (matches(n)) return true;
    return false;
  };
  window.__sweep = { ownerOf, rectOf, isSrOnly, isVisible, sigOf, textOf, visibleBox, safeAreaAware, inClosedDetails };
}

/** The layout checks, a to f (see the list above), at the current viewport. */
export function detect(opts) {
  const TOL = opts.alignTolerance;
  const hits = [];
  const doc = document.documentElement;
  const vw = doc.clientWidth;
  const vh = window.innerHeight;

  const cs = new WeakMap();
  const style = (el) => {
    let s = cs.get(el);
    if (!s) {
      s = getComputedStyle(el);
      cs.set(el, s);
    }
    return s;
  };
  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height, l: r.left, t: r.top, r: r.right, b: r.bottom };
  };
  const isVisible = (el) => {
    const s = style(el);
    if (s.display === "none" || s.visibility === "hidden" || s.visibility === "collapse") return false;
    if (Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const isSrOnly = (el) => {
    const r = el.getBoundingClientRect();
    return r.width <= 1 && r.height <= 1;
  };
  const insideSvg = (el) => el.tagName.toLowerCase() !== "svg" && el.closest("svg") !== null;
  const outOfFlow = (el) => {
    const p = style(el).position;
    return p === "absolute" || p === "fixed";
  };
  const inFixed = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) if (style(n).position === "fixed" || style(n).position === "sticky") return true;
    return false;
  };
  const inClosedDetails = (el) => {
    for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      if (n.tagName === "DETAILS" && !n.open && !(el.closest("summary") && el.closest("summary").parentElement === n)) return true;
    }
    return false;
  };
  const isCard = (el) => {
    const s = style(el);
    const bw = (v) => parseFloat(v) > 0;
    const boxed = bw(s.borderTopWidth) && bw(s.borderLeftWidth) && bw(s.borderRightWidth) && bw(s.borderBottomWidth);
    const shadow = s.boxShadow !== "none" && /rgba?\(/.test(s.boxShadow) && !/^rgba\(0, 0, 0, 0\)/.test(s.boxShadow);
    const bg = s.backgroundColor !== "rgba(0, 0, 0, 0)" && s.backgroundColor !== "transparent";
    return boxed || (shadow && (bg || parseFloat(s.borderTopLeftRadius) > 0));
  };
  /** The box the person can actually see `el` inside: every clipping ancestor (and itself) intersected. */
  const visibleBox = (el, r) => {
    let l = r.left, t = r.top, rt = r.right, b = r.bottom;
    for (let n = el; n && n !== document.documentElement; n = n.parentElement) {
      const s = style(n);
      if (s.overflowX !== "visible" || s.overflowY !== "visible") {
        const c = n.getBoundingClientRect();
        if (s.overflowX !== "visible") { l = Math.max(l, c.left); rt = Math.min(rt, c.right); }
        if (s.overflowY !== "visible") { t = Math.max(t, c.top); b = Math.min(b, c.bottom); }
      }
    }
    return { left: l, top: t, right: rt, bottom: b, width: rt - l, height: b - t };
  };
  const sig = (el) => {
    const parts = [];
    let n = el;
    for (let i = 0; n && n !== document.body && i < 3; i += 1, n = n.parentElement) {
      const cls = (typeof n.className === "string" ? n.className : "")
        .split(/\s+/)
        .filter((c) => c && !/^(?:sm|md|lg|xl|hover|focus|dark):/.test(c) && !/\[/.test(c))
        .slice(0, 3)
        .join(".");
      parts.push(n.tagName.toLowerCase() + (cls ? "." + cls : ""));
    }
    return parts.join(" < ");
  };
  const textOf = (el) => (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 60);
  const owner = (el) => (window.__sweep ? window.__sweep.ownerOf(el) : null);
  const push = (cat, kind, el, rects, detail) =>
    hits.push({ cat, kind, sig: sig(el), text: textOf(el), rects, detail: detail || "", owner: owner(el) });

  const all = Array.from(document.body.querySelectorAll("*")).filter(
    (el) => !["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "PATH", "CIRCLE", "LINE", "RECT", "G", "DEFS", "USE", "TITLE"].includes(el.tagName.toUpperCase()) && isVisible(el),
  );

  // ---- (a) horizontal page overflow -------------------------------------------------------
  {
    const clipsAbove = (el) => {
      for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
        const s = style(n);
        // `overflow-x: clip` is the shell's safety net (AppShell): it stops a wide child from
        // widening the page, but the child is still cut off, so it is still this defect.
        if (s.overflowX !== "visible" && s.overflowX !== "clip") return true;
        if (s.position === "fixed") return true;
      }
      return false;
    };
    const clippedSomewhere = all.some((el) => !outOfFlow(el) && !insideSvg(el) && el.getBoundingClientRect().right > vw + 1 && !clipsAbove(el));
    if (doc.scrollWidth > vw + 1 || clippedSomewhere) {
      const offenders = all
        .filter((el) => !outOfFlow(el) && !insideSvg(el) && !clipsAbove(el) && el.getBoundingClientRect().right > vw + 1)
        .slice(0, 400);
      // Report the deepest offenders: those with no offending child.
      const set = new Set(offenders);
      let leaves = offenders.filter((el) => !Array.from(el.children).some((c) => set.has(c))).slice(0, 4);
      if (leaves.length === 0) {
        // Something wider than the page whose own ancestors clip it: name the widest in-flow box.
        leaves = all
          .filter((el) => !insideSvg(el) && !inFixed(el) && visibleBox(el, el.getBoundingClientRect()).right > vw + 1)
          .sort((x, y) => y.getBoundingClientRect().right - x.getBoundingClientRect().right)
          .slice(0, 2);
      }
      if (leaves.length === 0 && doc.scrollWidth > vw + 1) hits.push({ cat: "a", kind: "page-overflow", sig: "html", text: "", rects: [], detail: `scrollWidth ${doc.scrollWidth} > ${vw}`, owner: null });
      for (const el of leaves) push("a", doc.scrollWidth > vw + 1 ? "page-overflow" : "content-clipped-by-shell", el, [rectOf(el)], `right edge ${Math.round(el.getBoundingClientRect().right)} > viewport ${vw}`);
    }
  }

  // ---- (b) element escapes its bordered / shadowed ancestor -------------------------------
  {
    const escapes = new Set();
    const cardOf = new Map();
    for (const el of all) {
      if (insideSvg(el) || outOfFlow(el) || isSrOnly(el) || inClosedDetails(el)) continue;
      if (el.tagName === "OPTION" || el.tagName === "TBODY" || el.tagName === "THEAD" || el.tagName === "TR") continue;
      const r = el.getBoundingClientRect();
      let n = el.parentElement;
      let clipped = false;
      let fixedOrAbs = false;
      while (n && n !== document.body) {
        const s = style(n);
        if (s.position === "fixed") { fixedOrAbs = true; break; }
        if (isCard(n)) break;
        if (s.overflowX !== "visible" || s.overflowY !== "visible") { clipped = true; break; }
        n = n.parentElement;
      }
      if (clipped || fixedOrAbs || !n || n === document.body) continue;
      const cr = n.getBoundingClientRect();
      if (cr.width < 8 || cr.height < 8) continue;
      // The card itself may clip: then nothing escapes visually (text cut is caught by (e)).
      const cardStyle = style(n);
      if (cardStyle.overflowX !== "visible" && cardStyle.overflowY !== "visible") continue;
      const out =
        r.right > cr.right + 1.5 || r.left < cr.left - 1.5 || r.bottom > cr.bottom + 1.5 || r.top < cr.top - 1.5;
      if (out) {
        escapes.add(el);
        cardOf.set(el, n);
      }
    }
    for (const el of escapes) {
      if (el.parentElement && escapes.has(el.parentElement) && cardOf.get(el.parentElement) === cardOf.get(el)) continue;
      const card = cardOf.get(el);
      const r = el.getBoundingClientRect();
      const cr = card.getBoundingClientRect();
      const d = [];
      if (r.right > cr.right + 1.5) d.push(`right +${Math.round(r.right - cr.right)}`);
      if (r.left < cr.left - 1.5) d.push(`left -${Math.round(cr.left - r.left)}`);
      if (r.bottom > cr.bottom + 1.5) d.push(`bottom +${Math.round(r.bottom - cr.bottom)}`);
      if (r.top < cr.top - 1.5) d.push(`top -${Math.round(cr.top - r.top)}`);
      push("b", "escapes-card", el, [rectOf(el), rectOf(card)], `${d.join(", ")} vs ${sig(card)}`);
    }
  }

  // ---- (c)/(d) control boxes in one row ---------------------------------------------------
  {
    const CONTROL = 'button, input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]):not([type="file"]), select, textarea, [role="combobox"], [role="switch"]';
    const isLinkButton = (a) => {
      if (a.tagName !== "A") return false;
      const s = style(a);
      return (parseFloat(s.borderTopWidth) > 0 || (s.backgroundColor !== "rgba(0, 0, 0, 0)" && s.backgroundColor !== "transparent")) && a.getBoundingClientRect().height >= 28;
    };
    const controlsIn = (el) => {
      const list = [];
      if (el.matches(CONTROL) || isLinkButton(el)) list.push(el);
      else {
        for (const c of el.querySelectorAll(CONTROL + ", a")) {
          if (c.tagName === "A" && !isLinkButton(c)) continue;
          if (!isVisible(c) || isSrOnly(c) || c.closest("[aria-hidden='true']")) continue;
          if (list.some((p) => p.contains(c))) continue;
          list.push(c);
        }
      }
      return list.filter((c) => isVisible(c) && !isSrOnly(c));
    };
    const hasLabel = (el) => el.querySelector("label, legend") !== null && !el.matches(CONTROL);

    for (const box of all) {
      const bs = style(box);
      if (!/(flex|grid)$/.test(bs.display) && bs.display !== "flex" && bs.display !== "grid") continue;
      if (box.tagName === "BUTTON" || box.tagName === "SELECT" || box.matches("[role='tablist'], [role='menu'], [role='listbox']")) continue;
      const kids = Array.from(box.children).filter((k) => isVisible(k) && !outOfFlow(k) && !insideSvg(k));
      if (kids.length < 2) continue;
      const items = [];
      for (const k of kids) {
        const cs0 = controlsIn(k);
        if (cs0.length === 0) continue;
        // A card, a landmark or a block of many controls is a region, not one field in a row.
        if (!k.matches(CONTROL) && (cs0.length > 4 || isCard(k) || k.matches("aside, nav, main, header, section, form, table") || k.getBoundingClientRect().height > 240)) continue;
        const first = cs0[0].getBoundingClientRect();
        items.push({ k, wr: k.getBoundingClientRect(), cr: first, ctl: cs0[0], label: hasLabel(k) });
      }
      if (items.length < 2) continue;
      // Visual lines: wrapped children share a line while their wrapper boxes overlap vertically.
      items.sort((x, y) => x.wr.top - y.wr.top || x.wr.left - y.wr.left);
      const lines = [];
      for (const it of items) {
        const line = lines[lines.length - 1];
        if (line && it.wr.top < line.bottom - 1) {
          line.items.push(it);
          line.bottom = Math.max(line.bottom, it.wr.bottom);
        } else lines.push({ items: [it], bottom: it.wr.bottom });
      }
      for (const line of lines) {
        if (line.items.length < 2) continue;
        const range = (f) => Math.max(...line.items.map(f)) - Math.min(...line.items.map(f));
        const dTop = range((i) => i.cr.top);
        const dBottom = range((i) => i.cr.bottom);
        const dMid = range((i) => (i.cr.top + i.cr.bottom) / 2);
        const labelled = line.items.every((i) => i.label);
        // Form fields hold their label above the input: the input TOPS must agree, not centres.
        const aligned = labelled ? dTop <= TOL : dTop <= TOL || dBottom <= TOL || dMid <= TOL;
        if (aligned) continue;
        const cat = labelled ? "d" : "c";
        const rects = [rectOf(box), ...line.items.map((i) => rectOf(i.ctl))];
        hits.push({
          cat,
          kind: labelled ? "field-row-misaligned" : "control-row-misaligned",
          sig: sig(box),
          text: line.items.map((i) => textOf(i.ctl) || i.ctl.getAttribute("aria-label") || i.ctl.tagName.toLowerCase()).join(" | ").slice(0, 80),
          rects,
          detail: `tops differ ${dTop.toFixed(1)}px, bottoms ${dBottom.toFixed(1)}px, centres ${dMid.toFixed(1)}px`,
          owner: owner(box),
        });
      }
    }
  }

  // ---- (e) clipped / overflowing / overlapping text ---------------------------------------
  {
    const ownText = (el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.nodeValue.trim() !== "");
    const SKIP_TAG = new Set(["INPUT", "TEXTAREA", "SELECT", "TABLE", "TBODY", "THEAD", "TR", "SVG", "HTML", "BODY", "OPTION"]);
    for (const el of all) {
      if (insideSvg(el) || SKIP_TAG.has(el.tagName.toUpperCase()) || isSrOnly(el)) continue;
      if (!ownText(el)) continue;
      const s = style(el);
      if (s.display === "inline") continue;
      const over = el.scrollWidth - el.clientWidth;
      if (over <= 1 || el.clientWidth === 0) continue;
      // A `::before` hit area (hit-44) is wider than a small control and counts toward scrollWidth; it paints nothing.
      const pb = getComputedStyle(el, "::before");
      if (pb.position === "absolute" && pb.content !== "none" && pb.content !== "normal") continue;
      const clip = s.overflowX === "hidden" || s.overflowX === "clip";
      const scroll = s.overflowX === "auto" || s.overflowX === "scroll";
      if (scroll || el.clientWidth < 24 || inClosedDetails(el) || el.closest("nav, aside")) continue;
      if (clip && s.textOverflow === "ellipsis") {
        if (!el.getAttribute("title") && !el.getAttribute("aria-label") && !el.closest("[title]")) push("i", "ellipsis-without-title", el, [rectOf(el)], `${over}px hidden`);
      } else if (clip) push("e", "text-clipped", el, [rectOf(el)], `${over}px cut off`);
      else push("e", "text-overflows-box", el, [rectOf(el)], `text ${over}px wider than its box`);
    }
    // Multi-line clamp that silently drops text.
    for (const el of all) {
      const s = style(el);
      if (!(s.webkitLineClamp && s.webkitLineClamp !== "none") || insideSvg(el)) continue;
      if (el.scrollHeight - el.clientHeight > 1 && !el.getAttribute("title")) push("i", "line-clamp-without-title", el, [rectOf(el)], `${el.scrollHeight - el.clientHeight}px hidden`);
    }

    // SVG text collisions: axis tick labels and data labels.
    const svgText = Array.from(document.querySelectorAll("svg text, svg tspan"))
      .filter((t) => t.tagName.toLowerCase() === "text" && t.textContent.trim() !== "" && isVisible(t.closest("svg")))
      .map((t) => ({ t, r: t.getBoundingClientRect() }))
      .filter((o) => o.r.width > 0 && o.r.height > 0);
    for (let i = 0; i < svgText.length; i += 1) {
      for (let j = i + 1; j < svgText.length; j += 1) {
        const A = svgText[i].r;
        const B = svgText[j].r;
        const w = Math.min(A.right, B.right) - Math.max(A.left, B.left);
        const h = Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top);
        if (w > 1 && h > 2) {
          const ta = svgText[i].t;
          const tb = svgText[j].t;
          push("e", "chart-text-overlap", ta, [rectOf(ta), rectOf(tb)], `"${ta.textContent.trim().slice(0, 20)}" overlaps "${tb.textContent.trim().slice(0, 20)}" by ${w.toFixed(1)}px`);
          break;
        }
      }
    }

    // Axis labels in an HTML chart: absolutely positioned siblings that must keep a gap.
    for (const row of document.querySelectorAll("figure [aria-hidden='true'].relative")) {
      const labels = Array.from(row.children).filter((c) => c.tagName === "SPAN" && c.textContent.trim() !== "" && isVisible(c));
      labels.sort((x, y) => x.getBoundingClientRect().left - y.getBoundingClientRect().left);
      for (let i = 1; i < labels.length; i += 1) {
        const prev = labels[i - 1].getBoundingClientRect();
        const next = labels[i].getBoundingClientRect();
        if (next.left - prev.right < 8) {
          push("e", "chart-labels-crowded", labels[i], [rectOf(labels[i - 1]), rectOf(labels[i])], `"${textOf(labels[i - 1])}" and "${textOf(labels[i])}" are ${Math.round(next.left - prev.right)}px apart`);
          break;
        }
      }
    }

    // HTML text colliding with other HTML text.
    const leaves = [];
    for (const el of all) {
      if (insideSvg(el) || SKIP_TAG.has(el.tagName.toUpperCase()) || isSrOnly(el) || !ownText(el)) continue;
      // Chart axes are absolutely positioned and aria-hidden by design: their collisions are the point.
      const inChart = el.closest("figure") !== null && !el.closest("[role='tooltip']");
      if (!inChart && (outOfFlow(el) || el.closest("[role='tooltip'], [role='listbox'], [role='menu'], [aria-hidden='true']"))) continue;
      if (inFixed(el) || inClosedDetails(el) || el.closest(".sr-only")) continue;
      let layered = false;
      for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
        const ns = style(n);
        if (ns.position === "absolute" && ns.zIndex !== "auto") layered = true;
      }
      if (layered && !inChart) continue;
      const range = document.createRange();
      range.selectNodeContents(el);
      const rects = Array.from(range.getClientRects())
        .map((r) => visibleBox(el, r))
        .filter((r) => r.width > 1 && r.height > 1);
      if (rects.length === 0) continue;
      leaves.push({ el, rects });
    }
    const seen = new Set();
    for (let i = 0; i < leaves.length; i += 1) {
      for (let j = i + 1; j < leaves.length; j += 1) {
        const a = leaves[i];
        const b = leaves[j];
        if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
        let hit = null;
        for (const A of a.rects) {
          for (const B of b.rects) {
            const w = Math.min(A.right, B.right) - Math.max(A.left, B.left);
            const h = Math.min(A.bottom, B.bottom) - Math.max(A.top, B.top);
            if (w > 2 && h > 5) {
              const small = Math.min(A.width * A.height, B.width * B.height);
              if ((w * h) / small > 0.5 && h > Math.min(A.height, B.height) * 0.5) hit = { w, h };
            }
          }
        }
        if (hit && !seen.has(a.el)) {
          seen.add(a.el);
          push("e", "text-overlaps-text", a.el, [rectOf(a.el), rectOf(b.el)], `overlaps "${textOf(b.el).slice(0, 24)}" by ${hit.w.toFixed(0)}x${hit.h.toFixed(0)}px`);
        }
      }
    }
  }

  return hits;
}

/**
 * (f) runs after the runner has scrolled to the bottom. Returns the gap, in px, between the last
 * piece of in-flow content and the lowest edge the person can see (the viewport bottom, or the
 * top of a bottom bar that is fixed over it).
 */
export function measureBottomGap() {
  const vh = window.innerHeight;
  let limit = vh;
  let limitEl = null;
  let last = 0;
  let lastEl = null;
  for (const el of document.body.querySelectorAll("*")) {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (s.position === "fixed") {
      if (r.bottom >= vh - 2 && r.height < vh / 2 && r.width > window.innerWidth / 2) { if (r.top < limit) limitEl = el; limit = Math.min(limit, r.top); }
      continue;
    }
    let fixedAnc = false;
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      const pos = getComputedStyle(n).position;
      if (pos === "fixed" || pos === "sticky") fixedAnc = true;
    }
    if (fixedAnc || el.closest(".sr-only, aside, nav, [aria-hidden='true'], dialog, [role='dialog'], [role='status'], [role='alert'], [data-sonner-toaster]")) continue;
    if (s.position === "sticky" || s.position === "absolute") continue;
    if (el.tagName.toLowerCase() === "svg" || el.closest("svg")) continue;
    const hasPaint =
      Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.nodeValue.trim() !== "") ||
      ["IMG", "CANVAS", "INPUT", "BUTTON", "SELECT", "TEXTAREA", "VIDEO"].includes(el.tagName) ||
      parseFloat(s.borderBottomWidth) > 0 ||
      s.boxShadow !== "none";
    if (!hasPaint) continue;
    // What the person can see: a row scrolled out of a clipping ancestor is not "content at the bottom".
    let bottom = r.bottom;
    let hidden = false;
    for (let n = el.parentElement; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflowY !== "visible") {
        const cr = n.getBoundingClientRect();
        if (r.top >= cr.bottom - 1) hidden = true;
        bottom = Math.min(bottom, cr.bottom);
      }
    }
    if (hidden) continue;
    if (bottom > last && bottom <= document.documentElement.scrollHeight + 1) {
      last = bottom;
      lastEl = el;
    }
  }
  const atEnd = window.scrollY + vh >= document.documentElement.scrollHeight - 2;
  const sigParts = [];
  for (let n = lastEl, i = 0; n && n !== document.body && i < 3; n = n.parentElement, i += 1) {
    const cls = (typeof n.className === "string" ? n.className : "").split(/\s+/).filter((c) => c && !/\[/.test(c)).slice(0, 3).join(".");
    sigParts.push(n.tagName.toLowerCase() + (cls ? "." + cls : ""));
  }
  return { gap: Math.round(limit - last), atEnd, scrollable: document.documentElement.scrollHeight > vh + 1, sig: sigParts.join(" < "), text: lastEl ? (lastEl.textContent || "").replace(/\s+/g, " ").trim().slice(0, 50) : "", bar: limitEl ? window.__sweep.ownerOf(limitEl) : null, barSig: limitEl ? window.__sweep.sigOf(limitEl) : "", owner: lastEl ? window.__sweep.ownerOf(lastEl) : null };
}

/**
 * The mobile checks that need no interaction: g, h, k, m and the static half of l. Run below 768px.
 */
export function detectMobile() {
  const H = window.__sweep;
  const hits = [];
  const doc = document.documentElement;
  const vw = doc.clientWidth;
  const vh = window.innerHeight;
  const push = (cat, kind, el, detail, rects) =>
    hits.push({ cat, kind, sig: H.sigOf(el), text: H.textOf(el), rects: rects || [H.rectOf(el)], detail, owner: H.ownerOf(el) });
  const inert = (el) => el.closest("[aria-hidden='true'], [inert], .sr-only");

  // ---- (g) touch targets under 44x44 ------------------------------------------------------
  {
    const MIN = 43.5;
    const TARGET = [
      "button",
      "a[href]",
      "summary",
      "[role='button']",
      "[role='tab']",
      "[role='menuitem']",
      "[role='menuitemcheckbox']",
      "[role='menuitemradio']",
      "[role='switch']",
      "[role='checkbox']",
      "[role='radio']",
      "input[type='checkbox']",
      "input[type='radio']",
      "input[type='button']",
      "input[type='submit']",
    ].join(",");
    const FIELD = "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='file']):not([type='range']):not([type='button']):not([type='submit']), select, textarea";
    const inProse = (a) => {
      if (a.tagName !== "A" || getComputedStyle(a).display !== "inline") return false;
      const p = a.parentElement;
      if (!p) return false;
      let text = "";
      for (const n of p.childNodes) if (n.nodeType === 3) text += n.nodeValue;
      return text.replace(/\s+/g, " ").trim().length >= 8;
    };
    const pseudoSize = (el) => {
      let w = 0;
      let h = 0;
      for (const which of ["::before", "::after"]) {
        const ps = getComputedStyle(el, which);
        if (ps.content === "none" || ps.content === "normal" || ps.position !== "absolute") continue;
        w = Math.max(w, parseFloat(ps.width) || 0);
        h = Math.max(h, parseFloat(ps.height) || 0);
      }
      return { w, h };
    };
    for (const el of document.body.querySelectorAll(TARGET + "," + FIELD)) {
      if (!H.isVisible(el) || H.isSrOnly(el) || el.disabled || el.getAttribute("aria-disabled") === "true" || inert(el)) continue;
      const isField = el.matches(FIELD);
      if (!isField && inProse(el)) continue;
      const vb = H.visibleBox(el);
      if (vb.width <= 0 || vb.height <= 0) continue;
      const r = el.getBoundingClientRect();
      // Parked off-screen (a skip link until it takes focus): nobody can tap it.
      if (r.bottom <= 0 || r.right <= 0 || r.left >= vw || r.top >= vh + 4000) continue;
      let w = r.width;
      let h = r.height;
      if (el.matches("input[type='checkbox'], input[type='radio'], [role='checkbox'], [role='radio']")) {
        const label = (el.labels && el.labels[0]) || el.closest("label");
        if (label) {
          const lr = label.getBoundingClientRect();
          w = Math.max(w, lr.width);
          h = Math.max(h, lr.height);
        }
      }
      const px = pseudoSize(el);
      w = Math.max(w, px.w);
      h = Math.max(h, px.h);
      if (isField) {
        if (h < MIN) push("g", "field-under-44px-tall", el, `${Math.round(w)}x${Math.round(h)}px`);
      } else if (w < MIN || h < MIN) push("g", "target-under-44px", el, `${Math.round(w)}x${Math.round(h)}px (min 44x44)`);
    }
  }

  // ---- (h) fields under 16px ----------------------------------------------------------------
  {
    const FIELD = "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='file']):not([type='range']):not([type='color']):not([type='button']):not([type='submit']), select, textarea, [contenteditable='true']";
    for (const el of document.body.querySelectorAll(FIELD)) {
      if (!H.isVisible(el) || H.isSrOnly(el) || inert(el)) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 15.99) push("h", "field-font-under-16px", el, `font-size ${fs}px: iOS zooms the page on focus`);
    }
  }

  // ---- (k) tables wider than the viewport with no scroll container ---------------------------------
  {
    for (const t of document.body.querySelectorAll("table, [role='table'], [role='grid']")) {
      if (!H.isVisible(t) || t.closest(".sr-only") || t.getBoundingClientRect().width <= 1) continue;
      const tr = t.getBoundingClientRect();
      let scroller = null;
      let clipper = null;
      for (let n = t.parentElement; n && n !== document.body; n = n.parentElement) {
        const s = getComputedStyle(n);
        if (s.overflowX === "auto" || s.overflowX === "scroll") { scroller = n; break; }
        if (s.overflowX === "hidden" || s.overflowX === "clip") { clipper = n; break; }
      }
      if (scroller) continue;
      if (clipper && tr.right > clipper.getBoundingClientRect().right + 1) push("k", "table-clipped-no-scroll", t, `table ${Math.round(tr.width)}px wide inside a clipping box ${Math.round(clipper.getBoundingClientRect().width)}px`);
      else if (!clipper && (tr.right > vw + 1 || tr.width > vw + 1)) push("k", "table-wider-than-viewport", t, `table ${Math.round(tr.width)}px wide, viewport ${vw}px, no scroll container`);
    }
  }

  // ---- (l, static) viewport meta and fixed bars on a screen edge ----------------------------------
  {
    const meta = document.querySelector("meta[name='viewport']");
    if (!meta || !/viewport-fit\s*=\s*cover/.test(meta.getAttribute("content") || "")) {
      hits.push({ cat: "l", kind: "viewport-fit-not-cover", sig: "meta[name=viewport]", text: "", rects: [], detail: "env(safe-area-inset-*) is 0 on iOS without viewport-fit=cover", owner: { shared: false, component: "app-local", file: "index.html" } });
    }
    for (const el of document.body.querySelectorAll("*")) {
      const s = getComputedStyle(el);
      if (s.position !== "fixed" && s.position !== "sticky") continue;
      if (!H.isVisible(el) || H.isSrOnly(el) || el.closest("[role='dialog'], [role='alertdialog'], dialog")) continue;
      const r = el.getBoundingClientRect();
      if (r.width < vw * 0.5 || (r.width >= vw * 0.98 && r.height >= vh * 0.9)) continue;
      if (s.position === "fixed" && r.bottom >= vh - 1 && !H.safeAreaAware(el, "bottom")) push("l", "fixed-bar-no-safe-area-bottom", el, "bar sits on the bottom edge and reads no env(safe-area-inset-bottom)");
      if (r.top <= 1 && r.height < vh * 0.5 && !H.safeAreaAware(el, "top")) push("l", "fixed-bar-no-safe-area-top", el, "bar sits on the top edge and reads no env(safe-area-inset-top)");
    }
  }

  // ---- (m) text size ------------------------------------------------------------------------------
  {
    const bodyFs = parseFloat(getComputedStyle(document.body).fontSize);
    if (bodyFs < 14) push("m", "body-font-under-14px", document.body, `body font-size ${bodyFs}px`, []);
    const ownText = (el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.nodeValue.trim() !== "");
    for (const el of document.body.querySelectorAll("*")) {
      const tag = el.tagName.toLowerCase();
      if (["script", "style", "option", "input", "textarea", "select", "svg", "tspan"].includes(tag) || !ownText(el) || H.isSrOnly(el) || !H.isVisible(el) || inert(el)) continue;
      let fs = parseFloat(getComputedStyle(el).fontSize);
      const svg = el.closest("svg");
      if (svg) {
        const vb = svg.viewBox && svg.viewBox.baseVal;
        const sr = svg.getBoundingClientRect();
        if (vb && vb.width > 0 && vb.height > 0) fs *= Math.min(sr.width / vb.width, sr.height / vb.height);
      }
      if (fs < 11.99) push("m", svg ? "svg-text-under-12px" : "text-under-12px", el, `font-size ${Math.round(fs * 10) / 10}px`);
      else if (!svg && fs < 12.99 && tag === "td") push("m", "table-cell-text-under-13px", el, `font-size ${Math.round(fs * 10) / 10}px`);
    }
  }
  return hits;
}

/**
 * (j) and the overlay half of (l): run while a dialog, drawer or menu is open.
 */
export function detectOverlay() {
  const H = window.__sweep;
  const hits = [];
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  const push = (cat, kind, el, detail) =>
    hits.push({ cat, kind, sig: H.sigOf(el), text: H.textOf(el), rects: [H.rectOf(el)], detail, owner: H.ownerOf(el) });
  const OVERLAY = "[role='dialog'], [role='alertdialog'], dialog[open], [role='menu'], [role='listbox'], [data-radix-popper-content-wrapper] > *";
  const found = Array.from(document.querySelectorAll(OVERLAY)).filter((el) => H.isVisible(el));
  for (const el of found) {
    if (found.some((o) => o !== el && o.contains(el))) continue;
    const r = el.getBoundingClientRect();
    const isDialog = el.matches("[role='dialog'], [role='alertdialog'], dialog");
    const kind = isDialog ? "dialog" : "menu";
    if (r.width > vw + 1 || r.left < -1 || r.right > vw + 1) push("j", `${kind}-exceeds-viewport-width`, el, `${Math.round(r.left)}..${Math.round(r.right)} of ${vw}px`);
    if (r.height > vh + 1 || r.top < -1 || r.bottom > vh + 1) push("j", `${kind}-exceeds-viewport-height`, el, `${Math.round(r.top)}..${Math.round(r.bottom)} of ${vh}px`);
    if (!isDialog) continue;
    const buttons = Array.from(el.querySelectorAll("button, [role='button'], a[href]")).filter(
      (b) => H.isVisible(b) && !/^(close|dismiss)/i.test(b.getAttribute("aria-label") || ""),
    );
    // The primary action: the submit button, or a filled accent / negative button. A navigation sheet has none.
    const primary = buttons.find((b) => b.matches("button[type='submit']")) || buttons.find((b) => !b.getAttribute("role") && /(^|\s)bg-(accent|negative)(\s|$)/.test(typeof b.className === "string" ? b.className : ""));
    if (primary) {
      const pr = primary.getBoundingClientRect();
      const vb = H.visibleBox(primary);
      if (pr.bottom > vh + 1 || pr.top < -1 || pr.right > vw + 1 || vb.height < pr.height - 2 || vb.width < pr.width - 2) {
        push("j", "dialog-primary-action-not-in-view", primary, `button ${Math.round(pr.top)}..${Math.round(pr.bottom)} of ${vh}px, visible ${Math.round(vb.height)}/${Math.round(pr.height)}px`);
      }
    }
    if (r.bottom >= vh - 1 && !H.safeAreaAware(el, "bottom")) push("l", "sheet-no-safe-area-bottom", el, "sheet sits on the bottom edge and reads no env(safe-area-inset-bottom)");
    if (r.top <= 1 && r.height >= vh - 1 && !H.safeAreaAware(el, "top")) push("l", "sheet-no-safe-area-top", el, "full-screen sheet reads no env(safe-area-inset-top)");
  }
  return hits;
}

/**
 * (l, dynamic) Focus the first few text fields the way a keyboard user would and check that the
 * browser's scroll-into-view did not leave one behind a fixed or sticky bar.
 */
export function focusObscured() {
  const H = window.__sweep;
  const hits = [];
  const FIELD = "input:not([type='hidden']):not([type='checkbox']):not([type='radio']):not([type='file']):not([type='range']):not([type='button']):not([type='submit']), select, textarea";
  const fields = Array.from(document.body.querySelectorAll(FIELD))
    .filter((el) => H.isVisible(el) && !el.disabled && !el.closest("[role='dialog'], [aria-hidden='true'], [inert]"))
    .slice(0, 12);
  const seen = new Set();
  window.scrollTo(0, 0);
  for (const el of fields) {
    el.focus();
    const r = el.getBoundingClientRect();
    // Where the caret is: the top-left of the field, not its centre (a tall textarea is bigger than the screen).
    const cx = Math.min(Math.max(r.left + Math.min(16, r.width / 2), 1), window.innerWidth - 1);
    const cy = Math.min(Math.max(r.top + Math.min(16, r.height / 2), 1), window.innerHeight - 1);
    const top = document.elementFromPoint(cx, cy);
    const off = r.bottom < 0 || r.top > window.innerHeight;
    if (!off && top && !el.contains(top) && !top.contains(el) && !(top.closest("label") && top.closest("label").contains(el))) {
      const key = H.sigOf(top);
      if (!seen.has(key)) {
        seen.add(key);
        hits.push({ cat: "l", kind: "focused-field-obscured", sig: H.sigOf(top), text: H.textOf(top), rects: [H.rectOf(el), H.rectOf(top)], detail: `focused field "${H.textOf(el) || el.getAttribute("aria-label") || el.name || el.type}" is covered by another element`, owner: H.ownerOf(top) });
      }
    }
    el.blur();
  }
  window.scrollTo(0, 0);
  return hits;
}

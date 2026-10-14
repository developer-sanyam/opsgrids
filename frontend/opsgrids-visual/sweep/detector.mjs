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
 */
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
  const push = (cat, kind, el, rects, detail) =>
    hits.push({ cat, kind, sig: sig(el), text: textOf(el), rects, detail: detail || "" });

  const all = Array.from(document.body.querySelectorAll("*")).filter(
    (el) => !["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "PATH", "CIRCLE", "LINE", "RECT", "G", "DEFS", "USE", "TITLE"].includes(el.tagName.toUpperCase()) && isVisible(el),
  );

  // ---- (a) horizontal page overflow -------------------------------------------------------
  {
    const clipsAbove = (el) => {
      for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
        const s = style(n);
        if (s.overflowX !== "visible") return true;
        if (s.position === "fixed") return true;
      }
      return false;
    };
    if (doc.scrollWidth > vw + 1) {
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
      if (leaves.length === 0) hits.push({ cat: "a", kind: "page-overflow", sig: "html", text: "", rects: [], detail: `scrollWidth ${doc.scrollWidth} > ${vw}` });
      for (const el of leaves) push("a", "page-overflow", el, [rectOf(el)], `right edge ${Math.round(el.getBoundingClientRect().right)} > viewport ${vw}`);
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
      if (!inChart && (outOfFlow(el) || el.closest("[role='tooltip'], [aria-hidden='true']"))) continue;
      if (inFixed(el) || inClosedDetails(el) || el.closest(".sr-only")) continue;
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
  let last = 0;
  let lastEl = null;
  for (const el of document.body.querySelectorAll("*")) {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden") continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (s.position === "fixed") {
      if (r.bottom >= vh - 2 && r.height < vh / 2 && r.width > window.innerWidth / 2) limit = Math.min(limit, r.top);
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
  return { gap: Math.round(limit - last), atEnd, scrollable: document.documentElement.scrollHeight > vh + 1, sig: sigParts.join(" < "), text: lastEl ? (lastEl.textContent || "").replace(/\s+/g, " ").trim().slice(0, 50) : "" };
}

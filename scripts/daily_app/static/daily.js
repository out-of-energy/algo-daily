(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const dataNode = $("visual-data");
  if (!dataNode) return;
  let visual;
  try {
    visual = JSON.parse(dataNode.textContent);
  } catch (err) {
    console.error("visual data parse failed", err);
    return;
  }

  const shell = $("visual-shell");
  const stage = $("visual-stage");
  const caption = $("visual-caption");
  const badge = $("visual-badge");
  const progress = $("visual-progress-bar");
  const counter = $("visual-counter");
  const playBtn = $("visual-play");
  const prevBtn = $("visual-prev");
  const nextBtn = $("visual-next");
  const speedBtn = $("visual-speed");
  const codeBox = $("visual-code");
  const panels = Array.isArray(visual.panels) ? visual.panels : [];
  const frames = Array.isArray(visual.frames) ? visual.frames : [];
  if (!stage || !frames.length || !panels.length) return;

  const reduceMotion = !!(window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const SVG = "http://www.w3.org/2000/svg";
  const WIDE = new Set(["array", "grid", "table", "linked_list", "graph"]);

  const make = (tag, className, text) => {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = String(text);
    return el;
  };
  const svgEl = (tag, attrs) => {
    const el = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach((k) => el.setAttribute(k, attrs[k]));
    return el;
  };
  const asSet = (v) => new Set(Array.isArray(v) ? v.map(String) : []);
  const cellSet = (v) => new Set((Array.isArray(v) ? v : []).map(([r, c]) => `${r},${c}`));
  const toggle = (el, cls, on) => el.classList.toggle(cls, !!on);
  const asNum = (v) => (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v.trim()) ? Number(v) : null);

  function flash(el) {
    if (reduceMotion || !el) return;
    el.classList.remove("flash");
    void el.offsetWidth;
    el.classList.add("flash");
  }

  function floatDelta(host, oldValue, newValue) {
    if (reduceMotion || !host) return;
    const a = asNum(oldValue);
    const b = asNum(newValue);
    if (a === null || b === null || a === b) return;
    const diff = b - a;
    const tag = make("span", "delta " + (diff > 0 ? "up" : "down"),
      (diff > 0 ? "+" : "") + (Number.isInteger(diff) ? diff : diff.toFixed(2)));
    host.appendChild(tag);
    setTimeout(() => tag.remove(), 1200);
  }

  function leave(el) {
    if (reduceMotion) {
      el.remove();
      return;
    }
    el.classList.add("leave");
    setTimeout(() => el.remove(), 260);
  }

  // Place an absolutely positioned element; brand-new elements jump into place
  // without a transition so they do not fly in from the corner.
  function place(el, x, y, fresh) {
    if (fresh) {
      el.style.transition = "none";
      el.style.transform = `translate(${x}px, ${y}px)`;
      void el.offsetWidth;
      el.style.transition = "";
    } else {
      el.style.transform = `translate(${x}px, ${y}px)`;
    }
  }

  function maxPointerStack(panelId) {
    let most = 0;
    frames.forEach((frame) => {
      const st = (frame.state || {})[panelId] || {};
      const counts = {};
      Object.values(st.pointers || {}).forEach((idx) => {
        const key = String(idx);
        counts[key] = (counts[key] || 0) + 1;
        most = Math.max(most, counts[key]);
      });
    });
    return most;
  }

  // ------------------------------------------------------------ pointers

  function pointerLayer(host) {
    const layer = make("div", "ptr-layer");
    host.appendChild(layer);
    const tags = new Map();
    return {
      // targets: name -> {x, bottom} in host coordinates; levels stack upward
      update(targets) {
        const levels = new Map();
        Object.keys(targets).forEach((name) => {
          const t = targets[name];
          const key = `${Math.round(t.x)}`;
          const level = levels.get(key) || 0;
          levels.set(key, level + 1);
          let tag = tags.get(name);
          const fresh = !tag;
          if (fresh) {
            tag = make("span", "ptr", name);
            layer.appendChild(tag);
            tags.set(name, tag);
          }
          tag.classList.toggle("null-ptr", !!t.isNull);
          const w = tag.offsetWidth || 20;
          place(tag, t.x - w / 2, t.y - (level + 1) * 21, fresh);
        });
        Array.from(tags.keys()).forEach((name) => {
          if (!(name in targets)) {
            leave(tags.get(name));
            tags.delete(name);
          }
        });
      },
    };
  }

  // ------------------------------------------------------------ array

  function arrayView(panel, body) {
    const reserve = Math.max(1, maxPointerStack(panel.id)) * 21 + 4;
    const row = make("div", "arr-row");
    row.style.paddingTop = `${reserve}px`;
    const win = make("div", "arr-window");
    row.appendChild(win);
    body.appendChild(row);
    const pointers = pointerLayer(row);
    let cells = [];

    function build(values) {
      cells.forEach((c) => c.wrap.remove());
      cells = values.map((value, i) => {
        const wrap = make("div", "arr-cell");
        const box = make("div", "arr-box", value);
        wrap.appendChild(box);
        wrap.appendChild(make("span", "arr-idx", i));
        const label = (panel.labels || [])[i];
        if (label) wrap.appendChild(make("span", "arr-label", label));
        row.appendChild(wrap);
        return { wrap, box };
      });
    }

    return {
      update(st, prev) {
        const values = st.values || [];
        if (cells.length !== values.length) build(values);
        const active = asSet(st.active);
        const done = asSet(st.done);
        const good = asSet(st.good);
        const bad = asSet(st.bad);
        const w = st.window;
        values.forEach((value, i) => {
          const { box } = cells[i];
          if (box.textContent !== String(value)) {
            const old = box.textContent;
            box.textContent = value;
            if (prev) {
              flash(box);
              floatDelta(cells[i].wrap, old, value);
            }
          }
          const k = String(i);
          toggle(box, "active", active.has(k));
          toggle(box, "done", done.has(k));
          toggle(box, "good", good.has(k));
          toggle(box, "bad", bad.has(k));
          toggle(box, "in-window", w && i >= w[0] && i <= w[1]);
        });
        if (w && cells[w[0]] && cells[w[1]]) {
          const a = cells[w[0]].box;
          const b = cells[w[1]].box;
          const wa = cells[w[0]].wrap;
          const wb = cells[w[1]].wrap;
          const left = wa.offsetLeft + a.offsetLeft - 5;
          const right = wb.offsetLeft + b.offsetLeft + b.offsetWidth + 5;
          win.style.left = `${left}px`;
          win.style.width = `${right - left}px`;
          win.style.top = `${wa.offsetTop + a.offsetTop - 5}px`;
          win.style.height = `${a.offsetHeight + 10}px`;
          win.classList.add("on");
        } else {
          win.classList.remove("on");
        }
        const targets = {};
        Object.keys(st.pointers || {}).forEach((name) => {
          const c = cells[st.pointers[name]];
          if (!c) return;
          targets[name] = { x: c.wrap.offsetLeft + c.wrap.offsetWidth / 2, y: reserve - 2 };
        });
        pointers.update(targets);
      },
    };
  }

  // ------------------------------------------------------------ linked list

  function linkedListView(panel, body) {
    const reserve = Math.max(1, maxPointerStack(panel.id)) * 21 + 4;
    const area = make("div", "ll-area");
    area.style.paddingTop = `${reserve}px`;
    const row = make("div", "ll-row");
    area.appendChild(row);
    const svg = svgEl("svg", { class: "ll-svg" });
    const defs = svgEl("defs");
    const marker = svgEl("marker", {
      id: `ll-arrow-${panel.id}`, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto",
    });
    marker.appendChild(svgEl("path", { d: "M0,0 L8,4 L0,8 z", class: "ll-arrowhead" }));
    defs.appendChild(marker);
    svg.appendChild(defs);
    const arrows = svgEl("g");
    svg.appendChild(arrows);
    area.appendChild(svg);
    body.appendChild(area);
    const pointers = pointerLayer(area);
    let nodes = [];
    let noneSlot = null;

    function build(labels) {
      row.innerHTML = "";
      nodes = labels.map((label) => {
        const el = make("div", "ll-node", label);
        row.appendChild(el);
        return el;
      });
      noneSlot = make("div", "ll-none", "None");
      row.appendChild(noneSlot);
    }

    const box = (el) => ({
      left: el.offsetLeft,
      right: el.offsetLeft + el.offsetWidth,
      top: el.offsetTop,
      bottom: el.offsetTop + el.offsetHeight,
      cx: el.offsetLeft + el.offsetWidth / 2,
      cy: el.offsetTop + el.offsetHeight / 2,
    });

    return {
      update(st, prev) {
        const labels = st.nodes || [];
        if (nodes.length !== labels.length) build(labels);
        labels.forEach((label, i) => {
          if (nodes[i].textContent !== String(label)) {
            nodes[i].textContent = label;
            if (prev) flash(nodes[i]);
          }
        });
        const active = asSet(st.active);
        const done = asSet(st.done);
        const good = asSet(st.good);
        const bad = asSet(st.bad);
        nodes.forEach((el, i) => {
          const k = String(i);
          toggle(el, "active", active.has(k));
          toggle(el, "done", done.has(k));
          toggle(el, "good", good.has(k));
          toggle(el, "bad", bad.has(k));
        });
        svg.setAttribute("width", area.scrollWidth);
        svg.setAttribute("height", area.scrollHeight);
        arrows.innerHTML = "";
        const next = st.next || [];
        const prevNext = prev ? prev.next || [] : next;
        next.forEach((j, i) => {
          const a = box(nodes[i]);
          const changed = prev && prevNext[i] !== j;
          let d;
          if (j === null || j === undefined) {
            const g = svgEl("g", { class: "ll-null" + (changed ? " fresh" : "") });
            g.appendChild(svgEl("line", { x1: a.cx, y1: a.bottom + 2, x2: a.cx, y2: a.bottom + 14 }));
            const t = svgEl("text", { x: a.cx, y: a.bottom + 27 });
            t.textContent = "∅";
            g.appendChild(t);
            arrows.appendChild(g);
            return;
          }
          const b = box(nodes[j]);
          if (j === i + 1) d = `M${a.right + 2},${a.cy - 5} L${b.left - 3},${b.cy - 5}`;
          else if (j === i - 1) d = `M${a.left - 2},${a.cy + 5} L${b.right + 3},${b.cy + 5}`;
          else if (j > i) d = `M${a.cx},${a.top - 2} C${a.cx},${a.top - 34} ${b.cx},${b.top - 34} ${b.cx},${b.top - 4}`;
          else if (j < i) d = `M${a.cx},${a.bottom + 2} C${a.cx},${a.bottom + 34} ${b.cx},${b.bottom + 34} ${b.cx},${b.bottom + 4}`;
          else d = `M${a.right - 6},${a.top} C${a.right + 18},${a.top - 26} ${a.left - 18},${a.top - 26} ${a.left + 6},${a.top}`;
          arrows.appendChild(svgEl("path", {
            d,
            class: "ll-link" + (changed ? " fresh" : ""),
            "marker-end": `url(#ll-arrow-${panel.id})`,
          }));
        });
        const targets = {};
        Object.keys(st.pointers || {}).forEach((name) => {
          const idx = st.pointers[name];
          const el = idx === null || idx === undefined ? noneSlot : nodes[idx];
          if (!el) return;
          const bx = box(el);
          targets[name] = { x: bx.cx, y: bx.top - 2, isNull: el === noneSlot };
        });
        pointers.update(targets);
      },
    };
  }

  // ------------------------------------------------------------ graph / tree

  function graphView(panel, body) {
    const radius = (node) => Number(node.r) || 19;
    const w = Math.max(panel.width || 300, 120);
    const h = Math.max(panel.height || 200, 90);
    const svg = svgEl("svg", { viewBox: `0 0 ${w} ${h}`, class: "graph-svg", role: "img" });
    svg.style.maxWidth = `${Math.max(w, 240)}px`;
    svg.setAttribute("aria-label", panel.label || visual.title || "graph");
    const defs = svgEl("defs");
    ["", "-hot"].forEach((suffix) => {
      const m = svgEl("marker", {
        id: `g-arrow-${panel.id}${suffix}`, markerWidth: 8, markerHeight: 8, refX: 7, refY: 4, orient: "auto",
      });
      m.appendChild(svgEl("path", { d: "M0,0 L8,4 L0,8 z", class: "g-arrowhead" + suffix }));
      defs.appendChild(m);
    });
    svg.appendChild(defs);
    const byId = new Map((panel.nodes || []).map((n) => [n.id, n]));
    const edgeEls = (panel.edges || []).map((edge) => {
      const a = byId.get(edge.from);
      const b = byId.get(edge.to);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.hypot(dx, dy) || 1;
      const ux = dx / len;
      const uy = dy / len;
      const pad = radius(b) + (panel.directed ? 3 : 0);
      const line = svgEl("line", {
        x1: a.x + ux * radius(a), y1: a.y + uy * radius(a), x2: b.x - ux * pad, y2: b.y - uy * pad, class: "g-edge",
      });
      if (panel.directed) line.setAttribute("marker-end", `url(#g-arrow-${panel.id})`);
      svg.appendChild(line);
      if (edge.label) {
        const t = svgEl("text", { x: (a.x + b.x) / 2 - uy * 10, y: (a.y + b.y) / 2 + ux * 10, class: "g-edge-label" });
        t.textContent = edge.label;
        svg.appendChild(t);
      }
      return line;
    });
    const nodeEls = new Map();
    (panel.nodes || []).forEach((node) => {
      const g = svgEl("g", { class: "g-node" });
      g.appendChild(svgEl("circle", { cx: node.x, cy: node.y, r: radius(node) }));
      const label = svgEl("text", { x: node.x, y: node.y, class: "g-label" });
      label.textContent = node.label;
      g.appendChild(label);
      const note = svgEl("text", { x: node.x, y: node.y + radius(node) + 13, class: "g-note" });
      g.appendChild(note);
      svg.appendChild(g);
      nodeEls.set(node.id, { g, label, note, base: node.label });
    });
    const wrap = make("div", "graph-wrap");
    wrap.appendChild(svg);
    body.appendChild(wrap);

    return {
      update(st, prev) {
        const sets = {};
        ["active", "visited", "done", "good", "bad"].forEach((k) => { sets[k] = asSet(st[k]); });
        const labels = st.labels || {};
        const notes = st.notes || {};
        nodeEls.forEach((el, id) => {
          Object.keys(sets).forEach((k) => el.g.classList.toggle(k, sets[k].has(id)));
          const text = labels[id] !== undefined ? labels[id] : el.base;
          if (el.label.textContent !== String(text)) {
            el.label.textContent = text;
            if (prev) flash(el.g);
          }
          const note = notes[id] !== undefined ? notes[id] : "";
          if (el.note.textContent !== String(note)) {
            el.note.textContent = note;
            if (prev && note) flash(el.note);
          }
        });
        const hot = new Set(st.active_edges || []);
        const good = new Set(st.good_edges || []);
        edgeEls.forEach((line, i) => {
          line.classList.toggle("active", hot.has(i));
          line.classList.toggle("good", good.has(i));
          if (panel.directed) {
            line.setAttribute("marker-end", `url(#g-arrow-${panel.id}${hot.has(i) || good.has(i) ? "-hot" : ""})`);
          }
        });
      },
    };
  }

  // ------------------------------------------------------------ table / grid

  function matrixView(panel, body, isGrid) {
    const table = make("table", isGrid ? "vgrid" : "vtable");
    body.appendChild(table);
    let tds = [];
    let shape = "";

    function build(matrix) {
      table.innerHTML = "";
      const colLabels = isGrid ? panel.col_labels || [] : panel.headers || [];
      if (colLabels.length) {
        const thead = make("thead");
        const tr = make("tr");
        if (isGrid && (panel.row_labels || []).length) tr.appendChild(make("th"));
        colLabels.forEach((h) => tr.appendChild(make("th", null, h)));
        thead.appendChild(tr);
        table.appendChild(thead);
      }
      const tbody = make("tbody");
      tds = matrix.map((row, r) => {
        const tr = make("tr");
        if (isGrid && (panel.row_labels || []).length) tr.appendChild(make("th", null, panel.row_labels[r] || ""));
        const cells = row.map((value, c) => {
          const td = make("td", !isGrid && c === 0 ? "row-head" : null, value);
          tr.appendChild(td);
          return td;
        });
        tbody.appendChild(tr);
        return cells;
      });
      table.appendChild(tbody);
    }

    return {
      update(st, prev) {
        const matrix = (isGrid ? st.grid : st.rows) || [];
        const nextShape = matrix.map((row) => row.length).join(",");
        if (nextShape !== shape) {
          build(matrix);
          shape = nextShape;
        }
        const keys = isGrid
          ? ["active", "visited", "path", "done", "good", "bad"]
          : ["active", "done", "good", "bad", "deps"];
        const sets = {};
        keys.forEach((k) => { sets[k] = cellSet(st[k]); });
        matrix.forEach((row, r) => row.forEach((value, c) => {
          const td = tds[r][c];
          if (td.textContent !== String(value)) {
            td.textContent = value;
            if (prev) flash(td);
          }
          const key = `${r},${c}`;
          keys.forEach((k) => toggle(td, k === "deps" ? "dep" : k, sets[k].has(key)));
        }));
      },
    };
  }

  // ------------------------------------------------------------ map

  function mapView(panel, body) {
    const list = make("div", "kv-list");
    const empty = make("span", "kv-empty", "空");
    body.appendChild(list);
    body.appendChild(empty);
    const chips = new Map();
    return {
      update(st, prev) {
        const items = st.items || {};
        const keys = Object.keys(items);
        const active = asSet(st.active);
        const bad = asSet(st.bad);
        const good = asSet(st.good);
        keys.forEach((key) => {
          let chip = chips.get(key);
          if (!chip) {
            const el = make("div", "kv" + (prev && !reduceMotion ? " enter" : ""));
            const k = make("span", "kv-k", key);
            const v = make("b", "kv-v", items[key]);
            el.appendChild(k);
            el.appendChild(v);
            chip = { el, v };
            chips.set(key, chip);
          } else if (chip.v.textContent !== String(items[key])) {
            const old = chip.v.textContent;
            chip.v.textContent = items[key];
            if (prev) {
              flash(chip.el);
              floatDelta(chip.el, old, items[key]);
            }
          }
          list.appendChild(chip.el);
          toggle(chip.el, "active", active.has(key));
          toggle(chip.el, "bad", bad.has(key));
          toggle(chip.el, "good", good.has(key));
        });
        Array.from(chips.keys()).forEach((key) => {
          if (!(key in items)) {
            leave(chips.get(key).el);
            chips.delete(key);
          }
        });
        empty.style.display = keys.length ? "none" : "";
      },
    };
  }

  // ------------------------------------------------------------ stack / queue

  function seqView(panel, body, isQueue) {
    const list = make("div", isQueue ? "q-list" : "st-list");
    const empty = make("span", "kv-empty", "空");
    body.appendChild(list);
    body.appendChild(empty);
    let els = [];
    let values = [];
    return {
      update(st, prev) {
        const items = (st.items || []).map(String);
        let shift = 0;
        if (isQueue && prev) {
          // Popping from the front of a queue: drop the leading elements
          // instead of rewriting every cell.
          for (let k = 1; k <= values.length; k += 1) {
            const rest = values.slice(k);
            if (rest.every((v, i) => items[i] === v)) {
              shift = k;
              break;
            }
          }
          if (values.length && values.every((v, i) => items[i] === v)) shift = 0;
        }
        for (let k = 0; k < shift; k += 1) leave(els[k]);
        els = els.slice(shift);
        values = values.slice(shift);
        items.forEach((item, i) => {
          if (!els[i]) {
            const el = make("div", "seq-item" + (prev && !reduceMotion ? " enter" : ""), item);
            list.appendChild(el);
            els[i] = el;
          } else if (values[i] !== item) {
            els[i].textContent = item;
            if (prev) flash(els[i]);
          }
        });
        els.slice(items.length).forEach(leave);
        els = els.slice(0, items.length);
        values = items;
        const active = asSet(st.active);
        const bad = asSet(st.bad);
        const good = asSet(st.good);
        els.forEach((el, i) => {
          toggle(el, "active", active.has(String(i)));
          toggle(el, "bad", bad.has(String(i)));
          toggle(el, "good", good.has(String(i)));
          toggle(el, "edge-a", i === (isQueue ? 0 : els.length - 1));
          toggle(el, "edge-b", isQueue && i === els.length - 1);
        });
        empty.style.display = items.length ? "none" : "";
      },
    };
  }

  // ------------------------------------------------------------ cards

  function cardsView(panel, body) {
    const grid = make("div", "card-grid");
    body.appendChild(grid);
    const cards = (panel.items || []).map((item, i) => {
      const card = make("div", "vcard");
      card.appendChild(make("b", null, `${i + 1}. ${item.title || "步骤"}`));
      if (item.detail) card.appendChild(make("span", null, item.detail));
      grid.appendChild(card);
      return card;
    });
    return {
      update(st) {
        ["active", "done", "good", "bad"].forEach((k) => {
          const set = asSet(st[k]);
          cards.forEach((card, i) => toggle(card, k, set.has(String(i))));
        });
      },
    };
  }

  // ------------------------------------------------------------ assemble

  const KIND_LABEL = {
    array: "数组", grid: "网格", table: "状态表", linked_list: "链表", graph: "图",
    map: "变量", stack: "栈", queue: "队列", cards: "步骤",
  };

  stage.innerHTML = "";
  const views = panels.map((panel) => {
    const box = make("div", "vpanel" + (WIDE.has(panel.kind) ? " wide" : "") + ` k-${panel.kind}`);
    const head = make("div", "vpanel-head");
    head.appendChild(make("span", "vpanel-label", panel.label || KIND_LABEL[panel.kind] || panel.kind));
    if (panel.kind === "stack") head.appendChild(make("span", "vpanel-hint", "栈顶在上"));
    if (panel.kind === "queue") head.appendChild(make("span", "vpanel-hint", "队头 → 队尾"));
    box.appendChild(head);
    const body = make("div", "vpanel-body");
    box.appendChild(body);
    stage.appendChild(box);
    let view;
    switch (panel.kind) {
      case "array": view = arrayView(panel, body); break;
      case "linked_list": view = linkedListView(panel, body); break;
      case "graph": view = graphView(panel, body); break;
      case "grid": view = matrixView(panel, body, true); break;
      case "table": view = matrixView(panel, body, false); break;
      case "map": view = mapView(panel, body); break;
      case "stack": view = seqView(panel, body, false); break;
      case "queue": view = seqView(panel, body, true); break;
      case "cards": view = cardsView(panel, body); break;
      default:
        body.appendChild(make("p", "empty-state", "暂不支持的面板类型"));
        view = { update() {} };
    }
    return { id: panel.id, view };
  });

  // ------------------------------------------------------------ code sync

  let codeLines = [];
  const source = $("solution-code");
  const usesLines = frames.some((f) => Array.isArray(f.lines) && f.lines.length);
  if (codeBox && source && usesLines) {
    const pre = make("pre", "vcode");
    source.textContent.replace(/\n$/, "").split("\n").forEach((text, i) => {
      const line = make("div", "vcode-line");
      line.appendChild(make("span", "vcode-no", i + 1));
      line.appendChild(make("span", "vcode-text", text || " "));
      pre.appendChild(line);
      codeLines.push(line);
    });
    codeBox.appendChild(pre);
    codeBox.hidden = false;
    if (shell) shell.classList.add("has-code");
  }

  function syncCode(lines) {
    if (!codeLines.length) return;
    const hot = new Set(lines || []);
    let first = null;
    codeLines.forEach((el, i) => {
      const on = hot.has(i + 1);
      el.classList.toggle("hot", on);
      if (on && !first) first = el;
    });
    if (first) {
      const top = first.offsetTop - codeBox.clientHeight / 2 + first.offsetHeight / 2;
      codeBox.scrollTo({ top: Math.max(0, top), behavior: reduceMotion ? "auto" : "smooth" });
    }
  }

  // ------------------------------------------------------------ playback

  let index = 0;
  let shown = null;
  let timer = null;
  const speeds = [0.5, 1, 1.5, 2];
  let speed = 1;

  function show(i, instant) {
    index = Math.max(0, Math.min(frames.length - 1, i));
    const frame = frames[index] || {};
    const before = !instant && shown !== null ? frames[shown] : null;
    const state = frame.state || {};
    views.forEach(({ id, view }) => {
      view.update(state[id] || {}, before ? (before.state || {})[id] || null : null);
    });
    shown = index;
    if (caption) caption.textContent = frame.caption || "";
    if (badge) {
      badge.textContent = frame.badge || "";
      badge.style.display = frame.badge ? "inline-flex" : "none";
    }
    if (shell) shell.dataset.tone = frame.tone || "info";
    if (counter) counter.textContent = `${index + 1} / ${frames.length}`;
    if (progress) progress.style.width = `${((index + 1) / frames.length) * 100}%`;
    if (prevBtn) prevBtn.disabled = index === 0;
    if (nextBtn) nextBtn.disabled = index === frames.length - 1;
    syncCode(frame.lines);
  }

  function dwell(frame) {
    const len = String((frame && frame.caption) || "").length;
    return Math.min(7000, Math.max(2200, 1600 + len * 60)) / speed;
  }

  function stop() {
    if (timer) clearTimeout(timer);
    timer = null;
    if (playBtn) playBtn.textContent = "播放";
  }

  function tick() {
    timer = setTimeout(() => {
      if (index >= frames.length - 1) {
        stop();
        return;
      }
      show(index + 1);
      tick();
    }, dwell(frames[index]));
  }

  function play() {
    if (timer) {
      stop();
      return;
    }
    if (index >= frames.length - 1) show(0);
    if (playBtn) playBtn.textContent = "暂停";
    tick();
  }

  const step = (delta) => {
    stop();
    show(index + delta);
  };

  if (prevBtn) prevBtn.addEventListener("click", () => step(-1));
  if (nextBtn) nextBtn.addEventListener("click", () => step(1));
  if (playBtn) playBtn.addEventListener("click", play);
  if (speedBtn) {
    speedBtn.addEventListener("click", () => {
      speed = speeds[(speeds.indexOf(speed) + 1) % speeds.length];
      speedBtn.textContent = `${speed}x`;
      if (timer) {
        clearTimeout(timer);
        tick();
      }
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.target && /INPUT|TEXTAREA|SELECT/.test(event.target.tagName)) return;
    if (event.key === "ArrowLeft") step(-1);
    else if (event.key === "ArrowRight") step(1);
    else if (event.key === " " && !(event.target && event.target.tagName === "BUTTON")) {
      event.preventDefault();
      play();
    }
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => show(index, true), 120);
  });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => show(index, true));

  // ------------------------------------------------------------ copy button

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = $(button.getAttribute("data-copy-target"));
      if (!target) return;
      const text = target.innerText;
      const done = () => {
        const old = button.textContent;
        button.textContent = "已复制";
        setTimeout(() => { button.textContent = old; }, 1400);
      };
      try {
        await navigator.clipboard.writeText(text);
        done();
      } catch (err) {
        // file:// pages often have no clipboard API; fall back to a textarea.
        const area = document.createElement("textarea");
        area.value = text;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.focus();
        area.select();
        try {
          document.execCommand("copy");
          done();
        } catch (fallbackErr) {
          console.warn("clipboard unavailable", fallbackErr);
        } finally {
          area.remove();
        }
      }
    });
  });

  show(0, true);
})();

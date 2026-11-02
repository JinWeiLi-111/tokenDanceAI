/** 通用小工具：DOM、转义、markdown、图标、toast */

const PROP_KEYS = new Set(["value", "checked", "disabled", "readOnly", "selected", "defaultSelected", "textContent"]);

/** 创建元素并挂属性/子节点。h('div.card#id', {onclick}, [child, 'text']) */
export function h(sel, props, children) {
  let tag = "div";
  let id = null;
  const classes = [];
  const head = sel.match(/^[a-zA-Z][\w-]*/);
  if (head) tag = head[0];
  const re = /([.#])([\w-]+)/g;
  let m;
  while ((m = re.exec(sel))) {
    if (m[1] === "#") id = m[2];
    else classes.push(m[2]);
  }
  const el = document.createElement(tag);
  if (id) el.id = id;
  if (classes.length) el.className = classes.join(" ");
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = (el.className ? el.className + " " : "") + v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (k === "html") el.innerHTML = v;
      else if (k === "style" && typeof v === "string") el.setAttribute("style", v);
      else if (PROP_KEYS.has(k)) el[k] = v;
      else el.setAttribute(k, v);
    }
  }
  append(el, children);
  return el;
}

export function append(parent, children) {
  if (children == null) return;
  if (!Array.isArray(children)) children = [children];
  for (const c of children) {
    if (c == null || c === false) continue;
    parent.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

/** 表单字段封装：label + 控件，可选 hint/error */
export function field(label, control, { hint, error } = {}) {
  return h("div.field", null, [
    h("label.field__label", null, label),
    control,
    hint ? h("span.field__hint", null, hint) : null,
    error ? h("span.field__error", null, error) : null,
  ]);
}

/* ---------- markdown (subset) ---------- */
/**
 * 渲染 markdown 子集：代码围栏、行内代码、标题、列表、引用、粗/斜体、链接。
 * 流式安全：未闭合的 ``` 会把剩余内容当作进行中的代码块渲染。
 */
export function md(src) {
  const text = String(src ?? "");
  const parts = text.split("```");
  let html = "";
  for (let i = 0; i < parts.length; i++) {
    if (i % 2 === 0) {
      html += renderTextBlock(parts[i]);
    } else {
      let content = parts[i];
      let lang = "";
      const nl = content.indexOf("\n");
      if (nl !== -1) {
        const first = content.slice(0, nl).trim();
        if (/^[a-zA-Z0-9_+.-]{0,20}$/.test(first)) {
          lang = first;
          content = content.slice(nl + 1);
        }
      }
      html +=
        '<div class="code-block">' +
        '<div class="code-block__bar">' +
        `<span>${esc(lang || "code")}</span>` +
        '<button type="button" class="code-block__copy" data-copy aria-label="复制代码">复制</button>' +
        "</div>" +
        `<pre><code>${esc(content.replace(/\n$/, ""))}</code></pre>` +
        "</div>";
    }
  }
  return html;
}

function renderTextBlock(text) {
  if (!text) return "";
  const lines = text.split("\n");
  const out = [];
  let listType = null;
  const listItems = [];
  const flushList = () => {
    if (!listType) return;
    out.push(`<${listType}>${listItems.map((it) => `<li>${inline(it)}</li>`).join("")}</${listType}>`);
    listType = null;
    listItems.length = 0;
  };
  const specialRe = /^(#{1,3})\s+|^>\s?|^[-*]\s+|^\d+\.\s+/;

  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (trimmed === "") { flushList(); continue; }

    const h = /^(#{1,3})\s+(.*)$/.exec(trimmed);
    if (h) { flushList(); const lvl = h[1].length; out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`); continue; }

    if (/^>\s?/.test(trimmed)) { flushList(); out.push(`<blockquote>${inline(trimmed.replace(/^>\s?/, ""))}</blockquote>`); continue; }

    if (/^[-*]\s+/.test(trimmed)) {
      if (listType !== "ul") { flushList(); listType = "ul"; }
      listItems.push(trimmed.replace(/^[-*]\s+/, ""));
      continue;
    }
    if (/^\d+\.\s+/.test(trimmed)) {
      if (listType !== "ol") { flushList(); listType = "ol"; }
      listItems.push(trimmed.replace(/^\d+\.\s+/, ""));
      continue;
    }

    flushList();
    const para = [trimmed];
    for (let j = i + 1; j < lines.length; j++) {
      const t = lines[j].trim();
      if (t === "" || specialRe.test(t)) break;
      para.push(t);
      i = j;
    }
    out.push(`<p>${inline(para.join("<br>"))}</p>`);
  }
  flushList();
  return out.join("");
}

function inline(text) {
  let s = esc(text);
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/_([^_\n]+)_/g, "<em>$1</em>");
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s;
}

/* ---------- icons (Lucide-like, inline svg strings) ---------- */
const ICONS = {
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  server: '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
  moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  sun: '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
  menu: '<line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/>',
  send: '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  chevron: '<polyline points="9 18 15 12 9 6"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  bolt: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  edit: '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>',
  close: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
};

/** icon(name, size=16) → Element */
export function icon(name, size = 16) {
  const path = ICONS[name];
  if (!path) return document.createTextNode("");
  return elFromSvg(`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`);
}

/** iconSvg(name, size) → HTML string (for use inside html prop) */
export function iconSvg(name, size = 16) {
  const path = ICONS[name];
  if (!path) return "";
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

function elFromSvg(svgStr) {
  const wrap = document.createElement("span");
  wrap.style.display = "inline-flex";
  wrap.innerHTML = svgStr;
  return wrap;
}

/* ---------- toast ---------- */
export function toast(message, kind = "info", ms = 3000) {
  const host = document.getElementById("toastHost");
  if (!host) return;
  const el = h(`toast.${kind === "error" ? "is-error" : kind === "ok" ? "is-ok" : ""}`, null, message);
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 200ms ease";
    setTimeout(() => el.remove(), 220);
  }, ms);
}

/* ---------- time grouping ---------- */
export function dayGroup(iso) {
  const d = new Date(iso);
  const today = new Date();
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const diff = startOfToday - d;
  const oneDay = 86400000;
  if (diff < 0) return "今天";
  if (diff < oneDay) return "今天";
  if (diff < oneDay * 2) return "昨天";
  if (diff < oneDay * 7) return "本周";
  return "更早";
}

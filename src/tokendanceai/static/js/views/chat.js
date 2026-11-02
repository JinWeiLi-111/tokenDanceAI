/** 主聊天视图：会话列表 + 历史消息 + SSE 流式发送（tool trace 折叠 + markdown） */
import {
  listSessions, listMessages, deleteSession, agentRun, getAccount,
} from "../api.js";
import { streamSSE } from "../sse.js";
import { getState, setState, subscribe } from "../store.js";
import { h, clear, esc, md, dayGroup, toast, icon, iconSvg } from "../util.js";
import { renderShell } from "../shell.js";
import { navigate, current } from "../router.js";
import { isLoggedIn, openAuthModal } from "./auth.js";

// 单例：避免每次 re-render 都新增订阅 / 全局监听（防止内存泄漏与 stale els）
let _els = null;
let _storeSubscribed = false;

const PROMPTS = [
  { icon: "✍️", title: "写并发布一篇博文", desc: "调用 blog MCP：先 create_post_draft，再 publish_post", prompt: "帮我写一篇关于 MCP 协议的博文，写完直接发布" },
  { icon: "📊", title: "汇总本周 PR", desc: "连接 GitHub MCP，拉取 PR 列表并生成摘要", prompt: "汇总一下我本周的 GitHub PR 并生成报告" },
  { icon: "🗂️", title: "整理文件系统", desc: "通过 filesystem MCP 自动归档 Downloads", prompt: "整理我的文件系统，把 Downloads 里的文件按类型归档" },
  { icon: "🔌", title: "检查 MCP 连通性", desc: "逐个测试 server，列出发现的 tools", prompt: "检查所有已连接的 MCP server 连通性，列出可用工具" },
];

export async function renderChat(app) {
  // 侧栏会话区（chat 专属，插入 shell 的 sidebar extra 插槽）
  const sidebarExtra = h("div", null, [
    h("div.sidebar__new", null, [
      h("button.btn-new#newSession", null, [
        h("span", null, "新建会话"),
        h("kbd", null, "⌘N"),
      ]),
    ]),
    h("div.sidebar__search", null, [
      h("input.search-input#sessionSearch", { type: "text", placeholder: "搜索会话…", autocomplete: "off" }),
    ]),
    h("div.sidebar__section-label", null, "会话历史"),
    h("nav.sidebar__sessions#sessionList"),
  ]);

  const { main } = renderShell(app, { active: "/chat", sidebarExtra });

  // 主列：顶栏 + 聊天区 + composer
  const chatInner = h("div.chat__inner#chatInner");
  const chat = h("section.chat#chat", null, [chatInner]);
  main.appendChild(h("header.topbar", null, [
    h("div.topbar__left", null, [
      h("span.topbar__title#topbarTitle", null, "新会话"),
      h("span.pill#topbarPill", null, "autopilot"),
    ]),
    h("div.topbar__right", null, [
      h("button.chip-btn#autopilotToggle", { title: "Autopilot 模式" }, [
        h("span", { html: iconSvg("bolt", 13) }),
        "Autopilot",
      ]),
    ]),
  ]));
  main.appendChild(chat);
  main.appendChild(composer());

  const els = {
    sessionList: sidebarExtra.querySelector("#sessionList"),
    search: sidebarExtra.querySelector("#sessionSearch"),
    chat,
    chatInner,
    composerInput: main.querySelector("#composerInput"),
    sendBtn: main.querySelector("#sendBtn"),
    autopilotToggle: main.querySelector("#autopilotToggle"),
    topbarTitle: main.querySelector("#topbarTitle"),
    topbarPill: main.querySelector("#topbarPill"),
    newSessionBtn: sidebarExtra.querySelector("#newSession"),
  };

  wireComposer(els);
  wireSidebar(els);
  wireCopyDelegation(chatInner);
  _els = els;
  if (!_storeSubscribed) {
    subscribe(() => { if (_els) renderSessionList(_els); });
    _storeSubscribed = true;
  }

  if (isLoggedIn()) {
    await refreshSessions(els);
    loadModelPill(els);
  } else {
    showGuestSidebar(els);
    guestModelPill(els);
  }
  showWelcome(els);
}

/* 访客态：侧栏会话区提示登录 */
function showGuestSidebar(els) {
  clear(els.sessionList);
  els.sessionList.appendChild(h("div.state", {
    style: "padding:var(--s5) var(--s2);font-size:0.78rem;text-align:center",
  }, [
    h("div", { style: "opacity:0.6;margin-bottom:var(--s2)" }, "登录后查看历史会话"),
    h("button.chip-btn", { onclick: () => openAuthModal(() => navigate(current())) }, "登录或注册"),
  ]));
}

function guestModelPill(els) {
  els.topbarPill.textContent = "访客模式";
  els.topbarPill.style.color = "var(--text-faint)";
  els.topbarPill.title = "登录后可发送消息";
}

/* ---------- composer ---------- */
function composer() {
  return h("div.composer-wrap", null, [
    h("div.composer", null, [
      h("textarea.composer__input#composerInput", {
        rows: "1", placeholder: "给 Agent 下达任务…  (⌘↵ 发送 / ⇧↵ 换行)",
      }),
      h("div.composer__bar", null, [
        h("div.composer__left"),
        h("div.composer__right", null, [
          h("button.send-btn#sendBtn", { disabled: true, "aria-label": "发送", title: "发送 (⌘↵)" },
            null, [icon("send", 16)]),
        ]),
      ]),
    ]),
    h("div.composer__hint", null, "Agent 会自主规划并调用工具，破坏性操作会请求确认"),
  ]);
}

function wireComposer(els) {
  const { composerInput: input, sendBtn, autopilotToggle: ap } = els;
  function grow() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
    sendBtn.disabled = input.value.trim() === "" || getState().running;
  }
  input.addEventListener("input", grow);
  sendBtn.addEventListener("click", () => send(els));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(els); }
    if (e.key === "n" && e.metaKey) { e.preventDefault(); newSession(els); }
  });

  ap.classList.add("is-on");
  ap.addEventListener("click", () => {
    ap.classList.toggle("is-on");
    setState({ autopoilt: ap.classList.contains("is-on") });
    updatePill(els);
  });

  grow();
}

function wireSidebar(els) {
  els.newSessionBtn.addEventListener("click", () => newSession(els));
  els.search.addEventListener("input", () => {
    const q = els.search.value.toLowerCase();
    els.sessionList.querySelectorAll(".session-item").forEach((item) => {
      const title = item.querySelector(".session-item__title").textContent.toLowerCase();
      item.style.display = title.includes(q) ? "" : "none";
    });
  });
}

/* 代码块复制：事件委托 */
function wireCopyDelegation(chatInner) {
  chatInner.addEventListener("click", async (e) => {
    const btn = e.target.closest(".code-block__copy");
    if (!btn) return;
    const block = btn.closest(".code-block");
    const code = block?.querySelector("pre")?.textContent || "";
    try {
      await navigator.clipboard.writeText(code);
      const orig = btn.textContent;
      btn.textContent = "已复制";
      setTimeout(() => { btn.textContent = orig; }, 1200);
    } catch { toast("复制失败", "error"); }
  });
}

/* ---------- sessions ---------- */
async function refreshSessions(els) {
  try {
    const sessions = await listSessions();
    setState({ sessions });
  } catch (err) { toast(err.message, "error"); }
}

function renderSessionList(els) {
  const { sessions, activeSessionId } = getState();
  clear(els.sessionList);
  if (sessions.length === 0) {
    els.sessionList.appendChild(h("div.state", { style: "padding:var(--s6) var(--s2);font-size:0.78rem" }, "暂无会话"));
    return;
  }
  let lastGroup = null;
  for (const s of sessions) {
    const g = dayGroup(s.update_at || s.create_at);
    if (g !== lastGroup) {
      els.sessionList.appendChild(h("div.session-group", null, g));
      lastGroup = g;
    }
    els.sessionList.appendChild(sessionItem(s, s.id === activeSessionId, els));
  }
}

function sessionItem(s, active, els) {
  const del = h("button.session-item__del", { title: "删除会话", onclick: async (e) => {
    e.stopPropagation();
    if (!confirm(`删除会话「${s.title}」？`)) return;
    try {
      await deleteSession(s.id);
      if (getState().activeSessionId === s.id) newSession(els);
      await refreshSessions(els);
    } catch (err) { toast(err.message, "error"); }
  } }, null, [icon("trash", 14)]);

  const item = h(`div.session-item${active ? ".is-active" : ""}`, { dataset: { id: s.id } }, [
    h("span.session-item__dot"),
    h("span.session-item__title", null, esc(s.title || "新会话")),
    del,
  ]);
  item.addEventListener("click", () => selectSession(els, s.id));
  return item;
}

/* ---------- selection / new ---------- */
function newSession(els) {
  setState({ activeSessionId: null });
  clear(els.chatInner);
  showWelcome(els);
  els.topbarTitle.textContent = "新会话";
  updatePill(els);
  renderSessionList(els);
  els.composerInput.focus();
}

async function selectSession(els, sessionId) {
  setState({ activeSessionId: sessionId });
  renderSessionList(els);
  clear(els.chatInner);
  els.chatInner.appendChild(h("div.state", null, [h("div.spinner"), "加载消息…"]));
  try {
    const msgs = await listMessages(sessionId);
    clear(els.chatInner);
    if (msgs.length === 0) {
      showWelcome(els);
    } else {
      renderHistory(els, msgs);
    }
    const s = getState().sessions.find((x) => x.id === sessionId);
    els.topbarTitle.textContent = s?.title || "会话";
  } catch (err) {
    clear(els.chatInner);
    els.chatInner.appendChild(h("div.state", null, esc(err.message)));
    toast(err.message, "error");
  }
  els.chat.scrollTop = els.chat.scrollHeight;
}

function renderHistory(els, msgs) {
  const traceById = new Map();
  for (const m of msgs) {
    if (m.role === "user") {
      appendUser(els, m.content);
    } else if (m.role === "assistant") {
      const asst = appendAssistant(els, m.content || "");
      let toolCalls = [];
      try { toolCalls = JSON.parse(m.tool_calls_json || "[]"); } catch {}
      for (const tc of toolCalls) {
        const trace = appendToolTrace(asst, tc);
        if (tc.id) traceById.set(tc.id, trace);
      }
    } else if (m.role === "tool") {
      let payload = {};
      try { payload = JSON.parse(m.tool_results_json || "{}"); } catch {}
      const trace = traceById.get(payload.tool_call_id);
      if (trace) {
        markTraceResult(trace, m.content, true);
      } else {
        const asst = appendAssistant(els, "");
        const t = appendToolTrace(asst, { name: payload.name, args: {}, id: payload.tool_call_id });
        markTraceResult(t, m.content, true);
      }
    }
  }
}

/* ---------- welcome ---------- */
function showWelcome(els) {
  clear(els.chatInner);
  els.chatInner.appendChild(h("div.hero", null, [
    h("div.hero__mark", null, "TD"),
    h("h1.hero__title", null, "今天想做什么？"),
    h("p.hero__sub", null, "通用 Agent 会通过已连接的 MCP server 自主完成任务 —— 写博文、整理文件、汇总 PR，一个 run 内从 draft 到 publish。"),
    h("div.prompts", null, PROMPTS.map((p) =>
      h("button.prompt-card", { onclick: () => {
        els.composerInput.value = p.prompt;
        els.composerInput.dispatchEvent(new Event("input"));
        els.composerInput.focus();
      } }, [
        h("div.prompt-card__icon", null, p.icon),
        h("div.prompt-card__title", null, p.title),
        h("div.prompt-card__desc", null, p.desc),
      ])
    )),
  ]));
}

/* ---------- message DOM ---------- */
function appendUser(els, text) {
  const m = h("div.msg", null, [
    h("div.msg__role", null, [h("div.avatar", null, userInitial()), "你"]),
    h("div.msg__body", { html: md(text) }),
  ]);
  els.chatInner.appendChild(m);
  els.chat.scrollTop = els.chat.scrollHeight;
  return m;
}

function appendAssistant(els, initialText = "") {
  const body = h("div.msg__body.msg__cursor", { html: md(initialText) });
  const traces = h("div.msg__traces");
  const m = h("div.msg", null, [
    h("div.msg__role", null, [h("div.avatar", null, "TD"), "Agent"]),
    body,
    traces,
  ]);
  m._body = body;
  m._traces = traces;
  m._text = initialText;
  m._traceById = new Map();
  els.chatInner.appendChild(m);
  els.chat.scrollTop = els.chat.scrollHeight;
  return m;
}

/* tool trace：可折叠，默认收起 */
function appendToolTrace(asst, tc) {
  const argsStr = tc.args ? JSON.stringify(tc.args, null, 2) : "";

  const status = h("span.tool-trace__status", null, "调用中…");
  const spinner = h("span.tool-trace__spinner");

  const head = h("div.tool-trace__head", { onclick: () => trace.classList.toggle("is-open") }, [
    h("span.tool-trace__chevron", { html: iconSvg("chevron", 14) }),
    spinner,
    h("span.tool-trace__name", null, esc(tc.name || "tool")),
    status,
  ]);

  const body = h("div.tool-trace__body");
  if (argsStr) {
    body.appendChild(h("div.tool-trace__label", null, "参数"));
    body.appendChild(h("div.tool-trace__args", null, esc(argsStr)));
  }

  const trace = h("div.tool-trace", null, [head, body]);
  trace._status = status;
  trace._spinner = spinner;
  trace._body = body;

  asst._traces.appendChild(trace);
  if (tc.id) asst._traceById.set(tc.id, trace);
  const sc = asst.closest(".chat");
  if (sc) sc.scrollTop = sc.scrollHeight;
  return trace;
}

function markTraceResult(trace, content, ok) {
  if (trace._spinner) trace._spinner.remove();
  trace._status.textContent = ok ? "完成" : "失败";
  trace._status.className = `tool-trace__status ${ok ? "is-ok" : "is-err"}`;
  trace._body.appendChild(h("div.tool-trace__label", { style: "margin-top:var(--s2)" }, null, "结果"));
  trace._body.appendChild(h("div.tool-trace__result", null, esc(String(content).slice(0, 4000))));
}

/* ---------- send + SSE ---------- */
async function send(els) {
  const text = els.composerInput.value.trim();
  if (!text || getState().running) return;
  if (!isLoggedIn()) {
    // 访客：保留输入内容，登录成功后重新渲染当前视图
    openAuthModal(() => navigate(current()));
    return;
  }
  const wasNew = getState().activeSessionId === null;

  const hero = els.chatInner.querySelector(".hero");
  if (hero) hero.remove();

  appendUser(els, text);
  const asst = appendAssistant(els, "");
  els.composerInput.value = "";
  els.composerInput.style.height = "auto";
  els.sendBtn.disabled = true;
  setState({ running: true });

  try {
    const res = await agentRun({
      message: text,
      session_id: getState().activeSessionId,
      autopoilt: getState().autopoilt,
    });
    await streamSSE(res, {
      token: (data) => {
        asst._text += data.content || "";
        asst._body.innerHTML = md(asst._text);
        els.chat.scrollTop = els.chat.scrollHeight;
      },
      tool_call: (data) => {
        appendToolTrace(asst, data);
        els.chat.scrollTop = els.chat.scrollHeight;
      },
      tool_result: (data) => {
        const trace = asst._traceById.get(data.tool_call_id);
        if (trace) markTraceResult(trace, data.content, true);
        els.chat.scrollTop = els.chat.scrollHeight;
      },
      done: async () => {
        asst._body.classList.remove("msg__cursor");
        if (wasNew) {
          try {
            const sessions = await listSessions(1, 0);
            if (sessions[0]) {
              setState({ activeSessionId: sessions[0].id });
              els.topbarTitle.textContent = sessions[0].title || "新会话";
            }
          } catch {}
        }
        await refreshSessions(els);
      },
    });
  } catch (err) {
    asst._body.classList.remove("msg__cursor");
    asst._body.innerHTML = `<p style="color:var(--danger)">${esc(err.message || "运行失败")}</p>`;
    toast(err.message || "运行失败", "error");
  } finally {
    setState({ running: false });
    els.sendBtn.disabled = els.composerInput.value.trim() === "";
  }
}

/* ---------- helpers ---------- */
function userInitial() {
  const p = JSON.parse(localStorage.getItem("td-profile") || "{}");
  return (p.email?.[0] || "U").toUpperCase();
}

function updatePill(els) {
  const on = getState().autopoilt;
  if (els.topbarPill.dataset.configured === "1") {
    els.topbarPill.textContent = `${els.topbarPill.dataset.model || ""} · ${on ? "autopilot" : "manual"}`;
  } else {
    els.topbarPill.textContent = on ? "autopilot" : "manual";
  }
}

async function loadModelPill(els) {
  try {
    const acc = await getAccount();
    els.topbarPill.dataset.configured = "1";
    els.topbarPill.dataset.model = acc.model;
    updatePill(els);
  } catch {
    els.topbarPill.textContent = "未配置模型";
    els.topbarPill.style.color = "var(--warn)";
    els.topbarPill.style.cursor = "pointer";
    els.topbarPill.title = "前往「模型配置」配置 LLM";
    els.topbarPill.onclick = () => navigate("/account");
  }
}

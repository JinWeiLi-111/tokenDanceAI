/** MCP server 管理视图：列表 / 新建 / 编辑 / 删除 / 测试连通性 */
import {
  listServers, createServer, updateServer, deleteServer, testServer,
} from "../api.js";
import { h, clear, esc, toast, field, iconSvg } from "../util.js";
import { renderShell } from "../shell.js";
import { guestGate } from "../guest.js";

export async function renderServers(app) {
  if (guestGate(app, {
    active: "/servers",
    title: "登录后管理 MCP Servers",
    desc: "MCP server 配置与连通性测试需要登录后才能访问与持久化。",
  })) return;
  const { main } = renderShell(app, { active: "/servers" });

  const page = h("div.page", null, [
    h("div.page__header", null, [
      h("div", null, [
        h("h1.page__title", null, "MCP Servers"),
        h("div.page__sub", null, "管理已连接的 MCP server，测试连通性与可用工具"),
      ]),
      h("button.btn-primary#createBtn", { onclick: () => toggleCreate(page) }, [
        h("span", { html: iconSvg("plus", 15) }), "新建 Server",
      ]),
    ]),
    h("div#createHost"),
    h("div#listHost", null, [h("div.state", null, [h("div.spinner"), "加载中…"])]),
  ]);
  main.appendChild(h("div.main-scroll", null, [page]));

  await loadList(page);
}

async function loadList(page) {
  const host = page.querySelector("#listHost");
  clear(host);
  host.appendChild(h("div.state", null, [h("div.spinner"), "加载中…"]));
  try {
    const servers = await listServers();
    clear(host);
    if (servers.length === 0) {
      host.appendChild(h("div.card", null, [
        h("div.state", null, [
          h("div.state__icon", null, "🔌"),
          "还没有配置 MCP server",
          h("div", { style: "font-size:0.8rem;margin-top:var(--s1)" }, "点击右上角「新建 Server」开始连接你的第一个 MCP。"),
        ]),
      ]));
      return;
    }
    for (const s of servers) host.appendChild(serverCard(s, page));
  } catch (err) {
    clear(host);
    host.appendChild(h("div.state", null, esc(err.message)));
    toast(err.message, "error");
  }
}

function serverCard(s, page) {
  const card = h("div.card", null, [
    h("div.server-row", null, [
      h("div.server-row__main", null, [
        h("div.server-row__name", null, [
          esc(s.name),
          h("span.pill", null, s.transport),
          s.enabled ? null : h("span.pill", { style: "color:var(--text-faint)" }, "disabled"),
        ]),
        h("div.server-row__url", null, esc(s.url)),
        h("div.server-row__meta", null, [
          toggle(s),
        ]),
      ]),
      h("div.server-row__actions", null, [
        h("button.btn-ghost", { onclick: () => runTest(card, s) }, "测试"),
        h("button.btn-ghost", { onclick: () => editServer(card, s, page) }, [
          h("span", { html: iconSvg("edit", 13) }), "编辑",
        ]),
        h("button.btn-ghost.btn-danger", { onclick: async () => {
          if (!confirm(`删除 server「${s.name}」？`)) return;
          try { await deleteServer(s.id); toast("已删除", "ok"); await loadList(page); }
          catch (err) { toast(err.message, "error"); }
        } }, [
          h("span", { html: iconSvg("trash", 13) }), "删除",
        ]),
      ]),
    ]),
    h("div.server-test-host"),
  ]);
  return card;
}

function toggle(s) {
  const t = h(`button.toggle${s.enabled ? ".is-on" : ""}`, {
    title: s.enabled ? "点击禁用" : "点击启用",
    onclick: async () => {
      t.classList.toggle("is-on");
      const enabled = t.classList.contains("is-on");
      try {
        await updateServer(s.id, { url: s.url, enabled });
        s.enabled = enabled;
        toast(enabled ? "已启用" : "已禁用", "ok");
      } catch (err) {
        t.classList.toggle("is-on");
        toast(err.message, "error");
      }
    },
  }, [h("span.toggle__thumb")]);
  const wrap = h("div", { style: "display:flex;align-items:center;gap:var(--s2)" }, [
    t,
    h("span.field__hint", null, s.enabled ? "已启用" : "已禁用"),
  ]);
  return wrap;
}

async function runTest(card, s) {
  const host = card.querySelector(".server-test-host");
  clear(host);
  host.appendChild(h("div.server-test", null, [h("div.spinner.spinner--sm"), " 测试中…"]));
  try {
    const r = await testServer(s.id);
    clear(host);
    if (!r.ok) {
      host.appendChild(h("div.server-test.is-err", null, [
        h("div.server-test__title", null, "连接失败"),
        h("div", null, esc(r.error || "未知错误")),
      ]));
      return;
    }
    const tools = r.tools || [];
    host.appendChild(h("div.server-test", null, [
      h("div.server-test__title", null, `连接成功 · 发现 ${tools.length} 个工具`),
      h("div.server-test__tools", null, tools.map((t) =>
        h("div.tool-line", null, [
          h("span.tool-line__name", null, t.name),
          t.description ? h("span.tool-line__desc", null, " — " + esc(t.description)) : null,
        ])
      )),
    ]));
  } catch (err) {
    clear(host);
    host.appendChild(h("div.server-test.is-err", null, [esc(err.message)]));
    toast(err.message, "error");
  }
}

/* ---------- create / edit form ---------- */
function toggleCreate(page) {
  const host = page.querySelector("#createHost");
  if (host.querySelector("form")) { clear(host); return; }
  clear(host);
  host.appendChild(serverForm({}, async (data) => {
    await createServer(data);
    toast("已创建", "ok");
    clear(host);
    await loadList(page);
  }));
  host.scrollIntoView({ behavior: "smooth", block: "start" });
}

function editServer(card, s, page) {
  const host = card.querySelector(".server-test-host");
  if (host.querySelector("form")) { clear(host); return; }
  clear(host);
  host.appendChild(serverForm({
    name: s.name, url: s.url, transport: s.transport,
  }, async (data) => {
    const patch = { ...data };
    if (!data.api_token) delete patch.api_token;
    await updateServer(s.id, patch);
    toast("已更新", "ok");
    clear(host);
    await loadList(page);
  }, { edit: true }));
}

function serverForm(initial, onSubmit, { edit = false } = {}) {
  const form = h("form.form-inline", { onsubmit: async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(form).entries());
    const submit = form.querySelector("button[type=submit]");
    submit.disabled = true;
    try { await onSubmit(fd); }
    catch (err) { toast(err.message, "error"); }
    finally { submit.disabled = false; }
  } }, [
    h("div.form-inline__row", null, [
      field("名称", h("input.field__input", { type: "text", name: "name", required: true, maxlength: "50", value: initial.name || "", placeholder: "blog-server" })),
      field("Transport", h("select.field__select", { name: "transport" }, [
        opt("http", initial.transport === "http"),
        opt("sse", initial.transport === "sse"),
      ])),
    ]),
    field("URL", h("input.field__input", { type: "text", name: "url", required: true, maxlength: "255", value: initial.url || "", placeholder: "https://example.com/mcp" })),
    field("API Token", h("input.field__input", { type: "password", name: "api_token", required: !edit, placeholder: edit ? "留空则保持原 token" : "server 访问令牌" }), { hint: edit ? "留空表示不修改" : undefined }),
    h("div.form-inline__actions", null, [
      h("button.btn-ghost", { type: "button", onclick: () => form.remove() }, "取消"),
      h("button.btn-primary", { type: "submit" }, edit ? "保存" : "创建"),
    ]),
  ]);
  return form;
}

function opt(value, selected) {
  const o = h("option", { value }, value.toUpperCase());
  if (selected) o.setAttribute("selected", "selected");
  return o;
}

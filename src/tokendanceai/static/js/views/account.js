/** LLM 账号配置视图：GET /api/account（404=未配置）+ POST upsert */
import { getAccount, upsertAccount } from "../api.js";
import { h, clear, esc, toast, field } from "../util.js";
import { renderShell } from "../shell.js";
import { guestGate } from "../guest.js";

export async function renderAccount(app) {
  if (guestGate(app, {
    active: "/account",
    title: "登录后配置模型",
    desc: "LLM provider、endpoint 与 API Key 会加密存储到你的账号下，请先登录。",
  })) return;
  const { main } = renderShell(app, { active: "/account" });

  const page = h("div.page", null, [
    h("div.page__header", null, [
      h("div", null, [
        h("h1.page__title", null, "模型配置"),
        h("div.page__sub", null, "为你的 Agent 配置 LLM provider、endpoint、模型与 API Key"),
      ]),
    ]),
    h("div#status"),
    h("div#formHost", null, [h("div.state", null, [h("div.spinner"), "加载中…"])]),
  ]);
  main.appendChild(h("div.main-scroll", null, [page]));

  await load(page);
}

async function load(page) {
  const status = page.querySelector("#status");
  const host = page.querySelector("#formHost");
  let configured = null;
  try {
    configured = await getAccount();
  } catch (err) {
    if (err.status !== 404) {
      clear(host);
      host.appendChild(h("div.state", null, esc(err.message)));
      toast(err.message, "error");
      return;
    }
  }

  clear(status);
  status.appendChild(h(`div.account-status${configured ? ".is-ok" : ""}`, null, [
    h("span.account-status__dot"),
    configured
      ? `已配置：${configured.provider} / ${configured.model}`
      : "尚未配置 LLM —— Agent 运行前需要先配置",
  ]));

  clear(host);
  host.appendChild(form(configured, status));
}

function form(configured, status) {
  const initial = configured || {};
  const f = h("form.card.account-form", { onsubmit: async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(f).entries());
    const submit = f.querySelector("button[type=submit]");
    submit.disabled = true;
    try {
      await upsertAccount(fd);
      toast("已保存", "ok");
      clear(status);
      status.appendChild(h("div.account-status.is-ok", null, [
        h("span.account-status__dot"),
        `已配置：${fd.provider} / ${fd.model}`,
      ]));
    } catch (err) {
      toast(err.message, "error");
    } finally {
      submit.disabled = false;
    }
  } }, [
    field("Provider", h("select.field__select", { name: "provider" }, [
      opt("openai", initial.provider === "openai"),
      opt("anthropic", initial.provider === "anthropic"),
    ]), { hint: "目前支持 openai / anthropic" }),
    field("Base URL", h("input.field__input", {
      type: "text", name: "base_url", required: true,
      value: initial.base_url || "", placeholder: "https://api.openai.com/v1",
    })),
    field("Model", h("input.field__input", {
      type: "text", name: "model", required: true, maxlength: "50",
      value: initial.model || "", placeholder: "gpt-4o",
    })),
    field("API Key", h("input.field__input", {
      type: "password", name: "api_key", required: true,
      placeholder: configured ? "输入新 key 覆盖（不回显）" : "sk-...",
    }), { hint: "加密存储，不会回显" }),
    h("button.btn-primary", { type: "submit" }, configured ? "更新配置" : "保存配置"),
  ]);
  return f;
}

function opt(value, selected) {
  const o = h("option", { value }, value);
  if (selected) o.setAttribute("selected", "selected");
  return o;
}

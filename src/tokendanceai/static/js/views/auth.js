/**
 * 登录/注册弹窗（modal）。不是独立路由——由头像悬停浮层或访客操作触发。
 * 成功后调用 onSuccess 回调（通常是重新渲染当前视图）。
 */
import { login, register, setToken, setProfile, getProfile } from "../api.js";
import { h, clear, toast, field, icon, esc } from "../util.js";

let _overlay = null;

/** 打开登录弹窗。onSuccess(token) 在登录成功后调用。 */
export function openAuthModal(onSuccess) {
  closeAuthModal();

  const prev = getProfile();
  let mode = prev ? "login" : "register";
  let error = null;

  const formHost = h("div#authFormHost");
  const errBox = h("div.auth__error", { style: "display:none" }, "");

  function redraw() {
    // tabs active
    card.querySelectorAll(".auth__tab").forEach((t) =>
      t.classList.toggle("is-active", t.dataset.mode === mode)
    );
    // error
    if (error) { errBox.textContent = error; errBox.style.display = ""; }
    else { errBox.style.display = "none"; }

    clear(formHost);
    const fields = [];
    if (mode === "register") {
      fields.push(field("用户名", h("input.field__input", {
        type: "text", name: "username", placeholder: "xiongkang", required: true,
        minlength: "1", maxlength: "50", autocomplete: "username",
      })));
    }
    fields.push(field("邮箱", h("input.field__input", {
      type: "email", name: "email", placeholder: "you@example.com", required: true,
      autocomplete: "email", value: prev?.email || "",
    })));
    fields.push(field("密码", h("input.field__input", {
      type: "password", name: "password", placeholder: "至少 8 位", required: true,
      minlength: "8", maxlength: "50",
      autocomplete: mode === "login" ? "current-password" : "new-password",
    })));

    const submit = h("button.btn-primary.auth__submit", { type: "submit" },
      mode === "login" ? "登录" : "注册并登录");

    const form = h("form.auth__form", { onsubmit: async (e) => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(form).entries());
      submit.disabled = true;
      error = null;
      try {
        if (mode === "register") await register(fd);
        const { access_token } = await login({ email: fd.email, password: fd.password });
        setToken(access_token);
        setProfile({ email: fd.email });
        toast("登录成功", "ok");
        const cb = onSuccess;
        closeAuthModal();
        cb?.(access_token);
      } catch (err) {
        error = err.message || "操作失败";
        redraw();
      } finally {
        submit.disabled = false;
      }
    } }, [...fields, submit]);

    formHost.appendChild(form);
    formHost.appendChild(h("div.auth__switch-hint", null, [
      mode === "login" ? "还没有账号？" : "已有账号？",
      h("a", {
        href: "#", onclick: (e) => { e.preventDefault(); mode = mode === "login" ? "register" : "login"; redraw(); },
      }, mode === "login" ? "去注册" : "去登录"),
    ]));

    // 自动聚焦第一个输入框
    const first = form.querySelector("input");
    if (first) setTimeout(() => first.focus(), 0);
  }

  const card = h("div.auth__card", null, [
    h("div.auth__brand", null, [
      h("div.brand-mark", null, "TD"),
      h("div.auth__brand-text", null, [
        h("div.brand-name", null, "TokenDance AI"),
        h("div.brand-sub", null, "通用 Agent"),
      ]),
      h("button.icon-btn#authClose", {
        title: "关闭", "aria-label": "关闭",
        style: "position:absolute;top:var(--s3);right:var(--s3);",
        onclick: closeAuthModal,
      }, null, [icon("close", 16)]),
    ]),
    h("div.auth__tabs", null, [
      tabBtn("登录", "login", () => { mode = "login"; redraw(); }),
      tabBtn("注册", "register", () => { mode = "register"; redraw(); }),
    ]),
    errBox,
    formHost,
  ]);

  _overlay = h("div.modal-overlay", { onclick: (e) => { if (e.target === _overlay) closeAuthModal(); } }, [card]);
  document.body.appendChild(_overlay);
  redraw();
}

function tabBtn(label, mode, onClick) {
  const b = h("button.auth__tab", { type: "button", onclick: onClick }, label);
  b.dataset.mode = mode;
  return b;
}

export function closeAuthModal() {
  if (_overlay) { _overlay.remove(); _overlay = null; }
}

export function isLoggedIn() {
  return !!localStorage.getItem("td-token");
}

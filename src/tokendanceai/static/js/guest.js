/** 访客门禁：未登录时渲染「请先登录」卡片，并经头像浮层触发登录弹窗。 */
import { h, iconSvg } from "./util.js";
import { renderShell } from "./shell.js";
import { isLoggedIn, openAuthModal } from "./views/auth.js";
import { navigate, current } from "./router.js";

/**
 * 若当前为访客，渲染 shell + 登录提示卡片，返回 true；
 * 否则返回 false（调用方应继续渲染受保护的业务内容）。
 */
export function guestGate(app, { active, title, desc }) {
  if (isLoggedIn()) return false;
  const { main } = renderShell(app, { active });
  main.appendChild(h("div.main-scroll", null, [
    h("div.page", null, [
      h("div.card", null, [
        h("div.state", { style: "padding:var(--s8) var(--s4)" }, [
          h("div.state__icon", { html: iconSvg("logout", 30) }),
          h("div", { style: "font-size:1.05rem;color:var(--text);font-weight:500" }, title),
          h("div", { style: "font-size:0.88rem;color:var(--text-muted);max-width:40ch" }, desc),
          h("button.btn-primary", {
            style: "margin-top:var(--s5)",
            onclick: () => openAuthModal(() => navigate(current())),
          }, [h("span", { html: iconSvg("logout", 15) }), "登录或注册"]),
        ]),
      ]),
    ]),
  ]));
  return true;
}

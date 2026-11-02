/**
 * 共享应用外壳：侧栏（品牌 / 导航 / 头像区）+ 主内容插槽 + 移动端抽屉。
 * 头像悬停浮层承载登录/登出入口 —— 访客也能直接看到对话页。
 */
import { h, clear, icon, iconSvg, esc } from "./util.js";
import { navigate } from "./router.js";
import { clearToken, getProfile } from "./api.js";
import { openAuthModal, isLoggedIn } from "./views/auth.js";

const NAV = [
  { path: "/chat", label: "会话", icon: "chat" },
  { path: "/servers", label: "MCP Servers", icon: "server" },
  { path: "/account", label: "模型配置", icon: "cpu" },
];

export function renderShell(app, { active = "/chat", sidebarExtra = null } = {}) {
  clear(app);

  const profile = getProfile();
  const authed = isLoggedIn();
  const email = profile?.email || "";
  const initial = (email[0] || "G").toUpperCase();

  const backdrop = h("div.drawer-backdrop");
  const sidebar = h("aside.sidebar");

  function openDrawer() { sidebar.classList.add("is-open"); backdrop.classList.add("is-open"); }
  function closeDrawer() { sidebar.classList.remove("is-open"); backdrop.classList.remove("is-open"); }
  function toggleDrawer() { sidebar.classList.contains("is-open") ? closeDrawer() : openDrawer(); }
  backdrop.addEventListener("click", closeDrawer);

  // 品牌
  sidebar.appendChild(h("div.sidebar__brand", null, [
    h("div.brand-mark", null, "TD"),
    h("div", null, [
      h("div.brand-name", null, "TokenDance AI"),
      h("div.brand-sub", null, "通用 Agent"),
    ]),
  ]));

  // 导航
  const nav = h("div.sidebar__nav");
  for (const item of NAV) {
    nav.appendChild(h("a.nav-link", {
      href: `#${item.path}`,
      onclick: (e) => { e.preventDefault(); closeDrawer(); navigate(item.path); },
      class: item.path === active ? "is-active" : "",
    }, [
      h("span.nav-link__icon", { html: iconSvg(item.icon, 17) }),
      h("span", null, item.label),
    ]));
  }
  sidebar.appendChild(nav);

  // 额外插槽（chat 的会话列表）
  if (sidebarExtra) {
    const extraWrap = h("div.sidebar__extra");
    extraWrap.appendChild(sidebarExtra);
    sidebar.appendChild(extraWrap);
  }

  // 头像区 + 悬停浮层（登录/登出）
  sidebar.appendChild(h("div.sidebar__footer", null, [
    h("div.footer-row", null, [
      userMenu(authed, email, initial, closeDrawer),
      h("div.footer-actions", null, [
        iconBtn("切换主题", themeIconName(), () => window.__toggleTheme()),
      ]),
    ]),
  ]));

  const main = h("main.main");
  const toggleBtn = h("button.drawer-toggle", {
    "aria-label": "打开菜单",
    onclick: toggleDrawer,
  }, null);
  toggleBtn.appendChild(icon("menu", 20));

  app.appendChild(toggleBtn);
  app.appendChild(backdrop);
  app.appendChild(h("div.app", null, [sidebar, main]));

  return { main, drawer: { open: openDrawer, close: closeDrawer, toggle: toggleDrawer }, sidebar };
}

function themeIconName() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "sun" : "moon";
}

function iconBtn(title, name, onclick) {
  const b = h(`button.icon-btn`, { title, "aria-label": title, onclick });
  b.appendChild(icon(name, 16));
  return b;
}

function userMenu(authed, email, initial, closeDrawer) {
  const chip = h("div.user-chip", null, [
    h("div.avatar", null, initial),
    h("span.user-chip__name", null, esc(authed ? email : "未登录")),
  ]);

  const popover = h("div.user-menu__popover");

  if (authed) {
    popover.appendChild(h("div.user-menu__email", null, esc(email)));
    popover.appendChild(h("div.user-menu__divider"));
    popover.appendChild(menuItem("模型配置", "cpu", "is-accent", () => { closeDrawer(); navigate("/account"); }));
    popover.appendChild(menuItem("登出", "logout", "is-danger", () => {
      clearToken();
      toastLogout();
      navigate("/chat");
    }));
  } else {
    popover.appendChild(h("div.user-menu__hint", null, "登录后可同步会话与配置"));
    popover.appendChild(menuItem("登录或注册", "logout", "is-accent", () => {
      closeDrawer();
      openAuthModal(() => navigate(location.hash.replace(/^#/, "") || "/chat"));
    }));
  }

  return h("div.user-menu", { tabindex: "0" }, [chip, popover]);
}

function menuItem(label, iconName, mod, onclick) {
  const btn = h(`button.user-menu__item.user-menu__item--${mod}`, { onclick }, [
    h("span", { html: iconSvg(iconName, 15) }),
    h("span", null, label),
  ]);
  return btn;
}

function toastLogout() {
  import("./util.js").then(({ toast }) => toast("已登出", "ok"));
}

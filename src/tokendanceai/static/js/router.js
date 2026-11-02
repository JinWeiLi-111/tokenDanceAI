/**
 * 极简 hash 路由。入口即对话页，不强制登录；
 * 登录通过侧栏头像悬停浮层触发（见 shell.js / auth.js）。
 */
import { renderChat } from "./views/chat.js";
import { renderServers } from "./views/servers.js";
import { renderAccount } from "./views/account.js";

const routes = new Map(); // path -> render

export function register(path, render) {
  routes.set(path, render);
}

export function current() {
  const h = location.hash.replace(/^#/, "") || "/chat";
  return h;
}

export function navigate(path) {
  if (location.hash !== `#${path}`) location.hash = path;
  else dispatch();
}

function dispatch() {
  const path = current();
  const render = routes.get(path) || routes.get("/chat");
  const app = document.getElementById("app");
  render(app, path);
}

window.addEventListener("hashchange", dispatch);

export function start() {
  if (!routes.has(current())) location.hash = "/chat";
  dispatch();
}

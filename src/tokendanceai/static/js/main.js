/** 入口：注册路由 + 启动。访客直达对话页，登录经头像悬停浮层触发（见 shell.js / auth.js）。 */
import { register, start } from "./router.js";
import { renderChat } from "./views/chat.js";
import { renderServers } from "./views/servers.js";
import { renderAccount } from "./views/account.js";

register("/chat", renderChat);
register("/servers", renderServers);
register("/account", renderAccount);

start();

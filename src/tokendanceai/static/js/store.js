/**
 * 极简全局状态 + 订阅。不引入框架，用 pub/sub 即可。
 * 持有：sessions 列表、当前 sessionId、autopilot 开关、运行中标志。
 */
const state = {
  sessions: [],
  activeSessionId: null,
  autopoilt: true,
  running: false,
};

const listeners = new Set();

export function getState() {
  return state;
}

export function setState(patch) {
  Object.assign(state, patch);
  for (const fn of listeners) fn(state);
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

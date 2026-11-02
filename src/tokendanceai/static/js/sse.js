/**
 * SSE over fetch — EventSource 不支持自定义请求头，而 /api/agent/run 需要
 * Bearer token，所以用 fetch + ReadableStream 手动解析 text/event-stream。
 *
 * 协议（对齐 agents/runner.py 的 _sse_*）：
 *   event: token        data: {"content": "..."}
 *   event: tool_call    data: {"name","args","id"}
 *   event: tool_result  data: {"tool_call_id","name","content"}
 *   event: done         data: {}
 *
 * handler 是一个对象：{ token, tool_call, tool_result, done }，每个 (data) => void
 */
export async function streamSSE(response, handler) {
  if (!response.ok || !response.body) {
    // 非 200：尝试读取错误正文
    let detail = `HTTP ${response.status}`;
    try {
      const text = await response.text();
      if (text) {
        try { detail = JSON.parse(text).detail ?? detail; }
        catch { detail = text; }
      }
    } catch { /* ignore */ }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // 按行切分，保留最后不完整的行在 buffer
    let nl;
    while ((nl = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, nl).replace(/\r$/, "");
      buffer = buffer.slice(nl + 1);

      if (line === "") {
        // 空行 = 一个 event 结束；但我们的 SSE 每个 event 已自带 data，
        // runner 用 \n\n 分隔，无需在此 accumulate 多行。重置 event 名。
        currentEvent = "message";
        continue;
      }
      if (line.startsWith(":")) continue; // 注释
      if (line.startsWith("event:")) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        const raw = line.slice(5).trim();
        let data = raw;
        if (raw) {
          try { data = JSON.parse(raw); } catch { /* 留作字符串 */ }
        }
        const fn = handler[currentEvent];
        if (fn) fn(data);
      }
    }
  }
}

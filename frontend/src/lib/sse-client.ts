import type { SSEEvent } from "@/types/gemuse";

export function parseSSEBlock(rawBlock: string): SSEEvent | null {
  const trimmed = rawBlock.trim();
  if (!trimmed) {
    return null;
  }

  let eventName = "message";
  const dataLines: string[] = [];

  for (const rawLine of trimmed.split(/\r?\n/)) {
    if (rawLine.startsWith(":")) {
      continue;
    }
    if (rawLine.startsWith("event:")) {
      eventName = rawLine.slice("event:".length).trim();
    } else if (rawLine.startsWith("data:")) {
      dataLines.push(rawLine.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length === 0) {
    return null;
  }

  const payloadStr = dataLines.join("\n");
  let parsedData: Record<string, unknown>;
  try {
    parsedData = JSON.parse(payloadStr) as Record<string, unknown>;
  } catch {
    parsedData = { message: payloadStr };
  }

  switch (eventName) {
    case "metadata":
    case "thinking":
    case "tool_call":
    case "delta":
    case "approval_required":
    case "done":
    case "error":
      return { event: eventName, data: parsedData } as SSEEvent;
    default:
      return null;
  }
}

export class SSEStreamParser {
  private buffer = "";

  feed(chunkText: string): SSEEvent[] {
    this.buffer += chunkText.replace(/\r\n/g, "\n");
    const events: SSEEvent[] = [];

    let delimiterIdx = this.buffer.indexOf("\n\n");
    while (delimiterIdx !== -1) {
      const rawBlock = this.buffer.slice(0, delimiterIdx);
      this.buffer = this.buffer.slice(delimiterIdx + 2);
      const parsed = parseSSEBlock(rawBlock);
      if (parsed !== null) {
        events.push(parsed);
      }
      delimiterIdx = this.buffer.indexOf("\n\n");
    }

    return events;
  }

  flush(): SSEEvent[] {
    if (!this.buffer.trim()) {
      this.buffer = "";
      return [];
    }
    const remaining = parseSSEBlock(this.buffer);
    this.buffer = "";
    return remaining ? [remaining] : [];
  }
}

export async function consumeSSEStream(
  response: Response,
  onEvent: (event: SSEEvent) => void
): Promise<void> {
  if (!response.body) {
    throw new Error("Empty SSE response body");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = new SSEStreamParser();

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      for (const evt of parser.flush()) {
        onEvent(evt);
      }
      break;
    }
    const text = decoder.decode(value, { stream: true });
    for (const evt of parser.feed(text)) {
      onEvent(evt);
    }
  }
}


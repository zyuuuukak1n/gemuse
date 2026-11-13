import { describe, expect, it } from "vitest";
import { consumeSSEStream, parseSSEBlock, SSEStreamParser } from "@/lib/sse-client";
import type { SSEEvent } from "@/types/gemuse";

describe("sse-client parser", () => {
  it("parses single SSE blocks across event types", () => {
    const thinking = parseSSEBlock(
      'event: thinking\ndata: {"stage":"planning","summary":"メモリ検索を実行中"}\n'
    );
    expect(thinking).toEqual({
      event: "thinking",
      data: { stage: "planning", summary: "メモリ検索を実行中" },
    });

    const approval = parseSSEBlock(
      'event: approval_required\ndata: {"approval_id":"app-1","action_type":"send_mail","payload":{"to":"a@example.com"},"expires_at":"2026-11-14T00:00:00Z","one_time_token":"tok123456789"}'
    );
    expect(approval).toMatchObject({
      event: "approval_required",
      data: {
        approval_id: "app-1",
        action_type: "send_mail",
      },
    });
  });

  it("reassembles chunked CRLF and split JSON frames via SSEStreamParser", () => {
    const parser = new SSEStreamParser();
    const part1 =
      'event: metadata\r\ndata: {"job_id":"j1","conversation_id":"c1","thinking_level":"high","routing_reason":"deep"}\r\n\r\nevent: delta\r\ndata: {"te';
    const part2 = 'xt":"こんにちは"}\r\n\r\n: keepalive\r\n\r\nevent: done\r\ndata: {"job_id":"j1","conversation_id":"c1","answer":"こんにちは","citations":["core_memory.md"]}\r\n\r\n';

    const events1 = parser.feed(part1);
    expect(events1).toHaveLength(1);
    expect(events1[0]?.event).toBe("metadata");

    const events2 = parser.feed(part2);
    expect(events2).toHaveLength(2);
    expect(events2[0]).toEqual({ event: "delta", data: { text: "こんにちは" } });
    expect(events2[1]).toMatchObject({
      event: "done",
      data: { answer: "こんにちは", citations: ["core_memory.md"] },
    });
    expect(parser.flush()).toEqual([]);
  });

  it("consumes ReadableStream<Uint8Array> from Response cleanly", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode('event: tool_call\ndata: {"tool_name":"browser_open","status":"started"}\n\n')
        );
        controller.enqueue(
          encoder.encode('event: done\ndata: {"job_id":"j2","conversation_id":"c2","answer":"OK","citations":[]}')
        );
        controller.close();
      },
    });

    const response = new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });

    const received: SSEEvent[] = [];
    await consumeSSEStream(response, (evt) => received.push(evt));

    expect(received).toHaveLength(2);
    expect(received[0]).toEqual({
      event: "tool_call",
      data: { tool_name: "browser_open", status: "started" },
    });
    expect(received[1]).toMatchObject({
      event: "done",
      data: { job_id: "j2", answer: "OK" },
    });
  });
});


import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ImportPage from "@/app/import/page";
import {
  GEMINI_OFFICIAL_ORIGIN,
  GEMUSE_INGEST_TOKEN_STORAGE_KEY,
  validateGeminiImportMessage,
} from "@/lib/gemini-import";

describe("ImportPage (/import) postMessage bridge", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("rejects untrusted origins and malformed message payloads", () => {
    expect(
      validateGeminiImportMessage("https://evil.example.com", {
        type: "GEMUSE_IMPORT_CHAT",
        payload: { title: "Hack", raw_text: "bad" },
      })
    ).toBeNull();

    expect(
      validateGeminiImportMessage(GEMINI_OFFICIAL_ORIGIN, {
        type: "OTHER_EVENT",
        payload: { title: "Ok", raw_text: "hello" },
      })
    ).toBeNull();

    expect(
      validateGeminiImportMessage(GEMINI_OFFICIAL_ORIGIN, {
        type: "GEMUSE_IMPORT_CHAT",
        payload: { title: "Empty", messages: [], raw_text: "   " },
      })
    ).toBeNull();
  });

  it("notifies opener READY, ignores bad origins, and auto-POSTs with stored token", async () => {
    window.localStorage.setItem(GEMUSE_INGEST_TOKEN_STORAGE_KEY, "saved-ingest-token");
    const openerPostMessage = vi.fn();
    Object.defineProperty(window, "opener", {
      configurable: true,
      value: { postMessage: openerPostMessage },
    });

    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          episodes_saved: 3,
          core_updates_applied: 1,
        }),
        { status: 200 }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ImportPage />);

    expect(openerPostMessage).toHaveBeenCalledWith(
      { type: "GEMUSE_IMPORT_READY" },
      GEMINI_OFFICIAL_ORIGIN
    );

    // Untrusted origin is silently ignored
    act(() => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: "https://attacker.example",
          data: {
            type: "GEMUSE_IMPORT_CHAT",
            payload: { title: "Evil", raw_text: "ignore me" },
          },
        })
      );
    });
    expect(fetchMock).not.toHaveBeenCalled();

    // Valid official Gemini message triggers POST /api/sync/ingest-chat
    const validEvent = new MessageEvent("message", {
      origin: GEMINI_OFFICIAL_ORIGIN,
      data: {
        type: "GEMUSE_IMPORT_CHAT",
        payload: {
          title: "Gemini Research Thread",
          source_url: "https://gemini.google.com/app/abc",
          messages: [{ role: "user", content: "Summarize pgvector" }],
          raw_text: null,
        },
      },
    });
    await act(async () => {
      window.dispatchEvent(validEvent);
      // Duplicate 1.5s fallback message is deduplicated cleanly
      window.dispatchEvent(validEvent);
    });

    await waitFor(() => {
      expect(screen.getByTestId("import-success")).toBeInTheDocument();
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const firstCall = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(firstCall[0]).toBe("/api/sync/ingest-chat");
    expect(firstCall[1].headers).toMatchObject({
      "Content-Type": "application/json",
      "X-Gemuse-Ingest-Token": "saved-ingest-token",
    });

    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Gemuse トップ画面へ戻る/ })).toHaveAttribute(
      "href",
      "/"
    );
  });

  it("prompts for token on 401/missing token, persists to localStorage, and retries", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Unauthorized" }), { status: 401 })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ episodes_saved: 2, core_updates_applied: 0 }),
          { status: 200 }
        )
      );
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.setItem(GEMUSE_INGEST_TOKEN_STORAGE_KEY, "wrong-token");

    render(<ImportPage />);

    await act(async () => {
      window.dispatchEvent(
        new MessageEvent("message", {
          origin: GEMINI_OFFICIAL_ORIGIN,
          data: {
            type: "GEMUSE_IMPORT_CHAT",
            payload: {
              title: "Retry Thread",
              source_url: "https://gemini.google.com/app/retry",
              messages: null,
              raw_text: "Some Gemini conversation text",
            },
          },
        })
      );
    });

    await waitFor(() => {
      expect(screen.getByLabelText("X-Gemuse-Ingest-Token を入力")).toBeInTheDocument();
    });

    const input = screen.getByLabelText("X-Gemuse-Ingest-Token を入力");
    fireEvent.change(input, { target: { value: "correct-token" } });
    fireEvent.click(screen.getByRole("button", { name: /保存してインポートを実行/ }));

    await waitFor(() => {
      expect(screen.getByTestId("import-success")).toBeInTheDocument();
    });

    expect(window.localStorage.getItem(GEMUSE_INGEST_TOKEN_STORAGE_KEY)).toBe("correct-token");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});


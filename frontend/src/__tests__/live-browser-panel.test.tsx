import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LiveBrowserPanel } from "@/components/browser/LiveBrowserPanel";
import type { BrowserSessionStatus } from "@/types/gemuse";

const INITIAL_STATUS: BrowserSessionStatus = {
  session_id: "sess-1",
  paused: false,
  pause_info: null,
  stopped: false,
  novnc_url: "/api/browser/vnc/sess-1/vnc.html",
  downloads: [],
  url: "https://example.com",
  title: "Example",
  active: true,
};

describe("LiveBrowserPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("toggles Take Control pause and resume states via backend endpoints", async () => {
    let serverPaused = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/pause")) {
        serverPaused = true;
        return new Response(JSON.stringify({ paused: true }), { status: 200 });
      }
      if (url.includes("/resume")) {
        serverPaused = false;
        return new Response(JSON.stringify({ paused: false }), { status: 200 });
      }
      if (url.includes("/status")) {
        return new Response(
          JSON.stringify({
            ...INITIAL_STATUS,
            paused: serverPaused,
            pause_info: serverPaused ? { reason: "手動操作 (Take Control) へ切替" } : null,
          }),
          { status: 200 }
        );
      }
      if (url.includes("/audit-logs")) {
        return new Response(JSON.stringify({ items: [] }), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    const onPauseChange = vi.fn();
    render(
      <LiveBrowserPanel
        sessionId="sess-1"
        initialStatus={INITIAL_STATUS}
        onPauseStateChange={onPauseChange}
      />
    );

    const pauseBtn = screen.getByRole("button", {
      name: /手動操作に切替\(Take Control\)/,
    });
    fireEvent.click(pauseBtn);

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /AI再開/ })
      ).toBeInTheDocument();
    });

    expect(onPauseChange).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: /AI再開/ }));

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /手動操作に切替\(Take Control\)/ })
      ).toBeInTheDocument();
    });
    expect(onPauseChange).toHaveBeenCalledWith(false);
  });
});

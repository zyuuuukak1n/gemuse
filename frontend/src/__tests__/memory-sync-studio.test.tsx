import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  buildGeminiBookmarklet,
  MemorySyncStudio,
} from "@/components/memory/MemorySyncStudio";

describe("MemorySyncStudio & Bookmarklet generator", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("builds valid javascript: bookmarklet pointing at domain and ingest endpoint", () => {
    const code = buildGeminiBookmarklet(
      "https://gemuse.zyuuuukak1n.dev///",
      "secret-ingest-999"
    );
    expect(code.startsWith("javascript:")).toBe(true);
    expect(code).toContain("https://gemuse.zyuuuukak1n.dev");
    expect(code).toContain("/api/sync/ingest-chat");
    expect(code).toContain("X-Gemuse-Ingest-Token");
    expect(code).toContain("secret-ingest-999");
  });

  it("updates generated bookmarklet dynamically in 公式Geminiブリッジ UI", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/proxy/memory/core")) {
        return new Response(JSON.stringify({ content: "# Core Profile\n- Test" }), {
          status: 200,
        });
      }
      if (url.includes("/api/proxy/memory/episodes")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      if (url.includes("/api/proxy/memory/skills")) {
        return new Response(JSON.stringify([]), { status: 200 });
      }
      return new Response(JSON.stringify({}), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<MemorySyncStudio />);

    await waitFor(() => {
      expect(screen.getByText(/Layer 1: Core Memory/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /公式Geminiブリッジ/ }));

    const domainInput = screen.getByLabelText("Gemuse公開ドメイン");
    const tokenInput = screen.getByLabelText("Ingest Token");

    fireEvent.change(domainInput, {
      target: { value: "https://custom.example.dev" },
    });
    fireEvent.change(tokenInput, {
      target: { value: "tok-ui-777" },
    });

    const outputBox = screen.getByLabelText("生成されたブックマークレット") as HTMLTextAreaElement;
    expect(outputBox.value).toContain("javascript:");
    expect(outputBox.value).toContain("https://custom.example.dev");
    expect(outputBox.value).toContain("tok-ui-777");
  });
});


import React from "react";
import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import {
  extractGoogleFileLinks,
  GoogleFileCard,
  parseGoogleWorkspaceUrl,
} from "@/components/chat/GoogleFileCard";
import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage } from "@/types/gemuse";

describe("GoogleFileCard & MessageBubble", () => {
  it("parses Docs/Sheets/Slides/Drive URLs into canonical preview URLs", () => {
    const doc = parseGoogleWorkspaceUrl("https://docs.google.com/document/d/doc-123/edit");
    expect(doc?.kind).toBe("docs");
    expect(doc?.previewUrl).toBe("https://docs.google.com/document/d/doc-123/preview");

    const sheet = parseGoogleWorkspaceUrl("https://docs.google.com/spreadsheets/d/sh-456/edit");
    expect(sheet?.kind).toBe("sheets");
    expect(sheet?.previewUrl).toBe("https://docs.google.com/spreadsheets/d/sh-456/preview");

    const slide = parseGoogleWorkspaceUrl("https://docs.google.com/presentation/d/sl-789/edit");
    expect(slide?.kind).toBe("slides");
    expect(slide?.previewUrl).toBe("https://docs.google.com/presentation/d/sl-789/preview");

    const driveFile = parseGoogleWorkspaceUrl("https://drive.google.com/file/d/drv-001/view");
    expect(driveFile?.kind).toBe("drive");
    expect(driveFile?.previewUrl).toBe("https://drive.google.com/file/d/drv-001/preview");
  });

  it("toggles sandboxed iframe preview and renders external open button", () => {
    render(
      <GoogleFileCard
        url="https://docs.google.com/document/d/doc-xyz/edit"
        title="研究ミーティング議事録"
        accountLabel="school"
      />
    );

    expect(screen.getByText("研究ミーティング議事録")).toBeInTheDocument();
    expect(screen.getByText("Google Docs")).toBeInTheDocument();
    expect(screen.getByText("school")).toBeInTheDocument();

    const openLink = screen.getByRole("link", { name: /Google で開く/ });
    expect(openLink).toHaveAttribute("href", "https://docs.google.com/document/d/doc-xyz/edit");
    expect(openLink).toHaveAttribute("target", "_blank");
    expect(openLink).toHaveAttribute("rel", "noopener noreferrer");

    expect(screen.queryByTitle("研究ミーティング議事録 プレビュー")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /プレビューを表示/ }));
    const iframe = screen.getByTitle("研究ミーティング議事録 プレビュー");
    expect(iframe).toHaveAttribute("src", "https://docs.google.com/document/d/doc-xyz/preview");
    expect(iframe).toHaveAttribute("sandbox", "allow-scripts allow-same-origin allow-popups");

    fireEvent.click(screen.getByRole("button", { name: /プレビューを閉じる/ }));
    expect(screen.queryByTitle("研究ミーティング議事録 プレビュー")).not.toBeInTheDocument();
  });

  it("renders rich GoogleFileCards inside MessageBubble for markdown links", () => {
    const content = [
      "[private] 以下のスプレッドシートとスライドを用意しました。",
      "- [家計簿2026](https://docs.google.com/spreadsheets/d/sh-abc/edit)",
      "- [発表資料](https://docs.google.com/presentation/d/sl-def/edit)",
    ].join("\n");

    expect(extractGoogleFileLinks(content)).toHaveLength(2);

    const msg: ChatMessage = {
      id: "m-1",
      role: "assistant",
      content,
      created_at: "2026-11-15T10:00:00Z",
    };
    render(<MessageBubble message={msg} />);

    const cards = screen.getAllByTestId("google-file-card");
    expect(cards).toHaveLength(2);
    expect(cards[0]).toHaveAttribute("data-google-kind", "sheets");
    expect(cards[1]).toHaveAttribute("data-google-kind", "slides");
  });
});


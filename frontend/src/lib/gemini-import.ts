export const GEMINI_OFFICIAL_ORIGIN = "https://gemini.google.com";
export const GEMUSE_INGEST_TOKEN_STORAGE_KEY = "gemuse_ingest_token";

export interface GeminiImportTurn {
  role: string;
  content: string;
}

export interface GeminiImportPayload {
  title: string;
  source_url: string;
  messages: GeminiImportTurn[] | null;
  raw_text: string | null;
}

export interface GeminiImportResult {
  episodes_saved: number;
  core_updates_applied: number;
  consolidation_summary?: string;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Strictly validate an incoming window `message` event before trusting its payload.
 */
export function validateGeminiImportMessage(
  origin: string,
  data: unknown
): GeminiImportPayload | null {
  if (origin !== GEMINI_OFFICIAL_ORIGIN) {
    return null;
  }
  if (!isPlainRecord(data) || data.type !== "GEMUSE_IMPORT_CHAT") {
    return null;
  }
  const rawPayload = data.payload;
  if (!isPlainRecord(rawPayload)) {
    return null;
  }

  const title =
    typeof rawPayload.title === "string" && rawPayload.title.trim()
      ? rawPayload.title.trim().slice(0, 300)
      : "Gemini Chat Import";
  const source_url =
    typeof rawPayload.source_url === "string" && rawPayload.source_url.trim()
      ? rawPayload.source_url.trim().slice(0, 1000)
      : GEMINI_OFFICIAL_ORIGIN;

  let messages: GeminiImportTurn[] | null = null;
  if (Array.isArray(rawPayload.messages)) {
    const validTurns: GeminiImportTurn[] = [];
    for (const item of rawPayload.messages) {
      if (!isPlainRecord(item)) continue;
      if (typeof item.role !== "string" || typeof item.content !== "string") continue;
      const cleanRole = item.role.trim();
      const cleanContent = item.content.trim();
      if (!cleanRole || !cleanContent) continue;
      validTurns.push({
        role: cleanRole.slice(0, 32),
        content: cleanContent.slice(0, 12000),
      });
    }
    if (validTurns.length > 0) {
      messages = validTurns.slice(-40);
    }
  }

  const raw_text =
    typeof rawPayload.raw_text === "string" && rawPayload.raw_text.trim()
      ? rawPayload.raw_text.trim().slice(0, 200000)
      : null;

  if (!messages && !raw_text) {
    return null;
  }

  return {
    title,
    source_url,
    messages,
    raw_text,
  };
}


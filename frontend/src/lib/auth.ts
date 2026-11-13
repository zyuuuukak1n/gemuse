import crypto from "crypto";
import type { NextRequest } from "next/server";

export const SESSION_COOKIE_NAME = "gemuse_session";
const SESSION_TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days

function getServerSecret(): string {
  return process.env.API_SECRET_TOKEN?.trim() || "";
}

export function timingSafeEqualStr(a: string, b: string): boolean {
  const bufA = Buffer.from(a, "utf8");
  const bufB = Buffer.from(b, "utf8");
  if (bufA.length !== bufB.length) {
    return false;
  }
  return crypto.timingSafeEqual(bufA, bufB);
}

export function verifyApiSecretInput(candidateToken: string): boolean {
  const expected = getServerSecret();
  if (!expected || !candidateToken) {
    return false;
  }
  return timingSafeEqualStr(candidateToken.trim(), expected);
}

export function createSessionCookieValue(ttlSeconds: number = SESSION_TTL_SECONDS): string {
  const secret = getServerSecret();
  if (!secret) {
    throw new Error("API_SECRET_TOKEN is not configured on frontend server");
  }
  const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;
  const sig = crypto
    .createHmac("sha256", secret)
    .update(`gemuse_session:${expiresAt}`)
    .digest("hex");
  return `${expiresAt}.${sig}`;
}

export function verifySessionCookieValue(cookieVal: string | undefined): boolean {
  const secret = getServerSecret();
  if (!secret || !cookieVal || !cookieVal.includes(".")) {
    return false;
  }
  const [expStr, providedSig] = cookieVal.split(".", 2);
  const expTs = Number.parseInt(expStr, 10);
  if (!Number.isFinite(expTs) || expTs < Math.floor(Date.now() / 1000)) {
    return false;
  }
  const expectedSig = crypto
    .createHmac("sha256", secret)
    .update(`gemuse_session:${expTs}`)
    .digest("hex");
  return timingSafeEqualStr(providedSig, expectedSig);
}

export function isSecureCookieRequest(request: NextRequest): boolean {
  if (process.env.FORCE_SECURE_COOKIE === "true") {
    return true;
  }
  const forwardedProto = request.headers.get("x-forwarded-proto");
  if (forwardedProto) {
    return forwardedProto.split(",")[0].trim().toLowerCase() === "https";
  }
  return request.nextUrl.protocol === "https:";
}

export function getBackendInternalUrl(): string {
  return (process.env.BACKEND_INTERNAL_URL || "http://backend:8000").replace(/\/+$/, "");
}


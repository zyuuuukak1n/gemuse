import { NextRequest, NextResponse } from "next/server";
import {
  createSessionCookieValue,
  isSecureCookieRequest,
  SESSION_COOKIE_NAME,
  verifyApiSecretInput,
  verifySessionCookieValue,
} from "@/lib/auth";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const hasCfJwt = Boolean(request.headers.get("cf-access-jwt-assertion"));
  const cookieVal = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  const authenticated = hasCfJwt || verifySessionCookieValue(cookieVal);
  return NextResponse.json({ authenticated });
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  let body: { token?: unknown } = {};
  try {
    body = (await request.json()) as { token?: unknown };
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload" }, { status: 400 });
  }

  const candidate = typeof body.token === "string" ? body.token : "";
  if (!verifyApiSecretInput(candidate)) {
    return NextResponse.json({ error: "認証トークンが正しくありません。" }, { status: 401 });
  }

  const sessionVal = createSessionCookieValue();
  const response = NextResponse.json({ authenticated: true });
  response.cookies.set({
    name: SESSION_COOKIE_NAME,
    value: sessionVal,
    httpOnly: true,
    secure: isSecureCookieRequest(request),
    sameSite: "strict",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
  return response;
}


import { NextRequest, NextResponse } from "next/server";
import { isSecureCookieRequest, SESSION_COOKIE_NAME } from "@/lib/auth";

export async function POST(request: NextRequest): Promise<NextResponse> {
  const response = NextResponse.json({ authenticated: false });
  response.cookies.set({
    name: SESSION_COOKIE_NAME,
    value: "",
    httpOnly: true,
    secure: isSecureCookieRequest(request),
    sameSite: "strict",
    path: "/",
    maxAge: 0,
  });
  return response;
}


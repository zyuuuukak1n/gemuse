import { NextRequest, NextResponse } from "next/server";
import {
  getBackendInternalUrl,
  SESSION_COOKIE_NAME,
  verifySessionCookieValue,
} from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

async function handleProxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  if (!path || path.length === 0 || path.some((segment) => segment === "..")) {
    return NextResponse.json({ error: "Invalid proxy path" }, { status: 400 });
  }

  const cfJwt = request.headers.get("cf-access-jwt-assertion")?.trim() || "";
  const sessionCookie = request.cookies.get(SESSION_COOKIE_NAME)?.value;
  const cookieValid = verifySessionCookieValue(sessionCookie);

  if (!cfJwt && !cookieValid) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const backendBase = getBackendInternalUrl();
  const targetPath = path[0] === "health" ? "/health" : `/api/${path.join("/")}`;
  const targetUrl = `${backendBase}${targetPath}${request.nextUrl.search}`;

  const upstreamHeaders = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) {
    upstreamHeaders.set("Content-Type", contentType);
  }
  const accept = request.headers.get("accept");
  if (accept) {
    upstreamHeaders.set("Accept", accept);
  }

  if (cfJwt) {
    upstreamHeaders.set("Cf-Access-Jwt-Assertion", cfJwt);
  }

  const serverToken = process.env.API_SECRET_TOKEN?.trim() || "";
  if (serverToken) {
    upstreamHeaders.set("Authorization", `Bearer ${serverToken}`);
  }

  const method = request.method.toUpperCase();
  const hasBody = !["GET", "HEAD"].includes(method);
  const body = hasBody ? await request.arrayBuffer() : undefined;

  let upstreamResp: Response;
  try {
    upstreamResp = await fetch(targetUrl, {
      method,
      headers: upstreamHeaders,
      body,
      redirect: "manual",
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Upstream connection error";
    return NextResponse.json(
      { error: "Backend unreachable", detail: message },
      { status: 502 }
    );
  }

  const responseHeaders = new Headers();
  const passthroughHeaderNames = [
    "content-type",
    "cache-control",
    "content-disposition",
    "x-accel-buffering",
  ];
  for (const name of passthroughHeaderNames) {
    const val = upstreamResp.headers.get(name);
    if (val) {
      responseHeaders.set(name, val);
    }
  }

  if ((upstreamResp.headers.get("content-type") || "").includes("text/event-stream")) {
    responseHeaders.set("Cache-Control", "no-cache, no-transform");
    responseHeaders.set("X-Accel-Buffering", "no");
  }

  return new Response(upstreamResp.body, {
    status: upstreamResp.status,
    statusText: upstreamResp.statusText,
    headers: responseHeaders,
  });
}

export async function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  return handleProxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext): Promise<Response> {
  return handleProxy(request, context);
}

export async function PUT(request: NextRequest, context: RouteContext): Promise<Response> {
  return handleProxy(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext): Promise<Response> {
  return handleProxy(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext): Promise<Response> {
  return handleProxy(request, context);
}


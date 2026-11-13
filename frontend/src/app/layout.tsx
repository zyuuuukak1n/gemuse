import type { Metadata, Viewport } from "next";
import "./globals.css";

const appDomain = process.env.NEXT_PUBLIC_APP_DOMAIN || "https://gemuse.zyuuuukak1n.dev";
const ogpFqdn = `${appDomain.replace(/\/+$/, "")}/ogp.png`;

export const metadata: Metadata = {
  metadataBase: new URL(appDomain),
  title: "gemuse | Personal AI Agent Platform",
  description:
    "Gemini 4 Hydrogen powered personal AI agent workspace with 3-layer memory, MCP hub, isolated noVNC browser, and proactive assistant.",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "gemuse",
  },
  openGraph: {
    title: "gemuse | Personal AI Agent Platform",
    description:
      "Self-hosted personal AI agent powered by Gemini 4 Hydrogen, MCP Hub, and live Take Control browser.",
    url: appDomain,
    siteName: "gemuse",
    images: [{ url: ogpFqdn, width: 1200, height: 630, alt: "gemuse AI workspace" }],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "gemuse | Personal AI Agent Platform",
    description:
      "Self-hosted personal AI agent powered by Gemini 4 Hydrogen, MCP Hub, and live Take Control browser.",
    images: [ogpFqdn],
  },
  icons: {
    icon: "/icons/icon-192.svg",
    apple: "/icons/icon-192.svg",
  },
};

export const viewport: Viewport = {
  themeColor: "#10b981",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ja" className="dark" suppressHydrationWarning>
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased dark:bg-[#090d0b] dark:text-slate-100">
        {children}
      </body>
    </html>
  );
}


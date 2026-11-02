import type { Metadata, Viewport } from "next";
import { Nav } from "@/components/Nav";
import { StatusBanner } from "@/components/StatusBanner";
import "./globals.css";

export const metadata: Metadata = {
  title: "SENDU",
  description: "New second-hand designer listings, as they appear.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f5f2" },
    { media: "(prefers-color-scheme: dark)", color: "#0f0f0e" },
  ],
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-dvh">
        <Nav />
        <StatusBanner />
        <main className="mx-auto w-full max-w-6xl px-4 pb-16 pt-4">{children}</main>
      </body>
    </html>
  );
}

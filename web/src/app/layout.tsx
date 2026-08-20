import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Sans } from "next/font/google";

import { Providers } from "@/components/providers";
import { NO_FLASH_SCRIPT } from "@/store/theme";

import "./globals.css";

// The Ledgermark terminal's own two faces, read from its stylesheet:
//   --font-sans: "Instrument Sans"   --font-mono: "IBM Plex Mono"
// Self-hosted by next/font rather than linked from Google, because a webfont fetched at paint
// time is a flash of fallback on every cold load — and on a page that is mostly numbers, the
// reflow when a proportional fallback swaps for a tabular face is very visible.
const sans = Instrument_Sans({ variable: "--font-sans", subsets: ["latin"] });
const mono = IBM_Plex_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: { default: "Chukul Terminal", template: "%s · Chukul" },
  description: "NEPSE research terminal — deterministic analysis over the daily archive.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0e1419" },
    { media: "(prefers-color-scheme: light)", color: "#f4f6f9" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // `dark` is on the server-rendered html so the very first paint is already dark; the inline
    // script below then corrects it to `light` before hydration if that is what was saved.
    // suppressHydrationWarning is required and is scoped to this element only: the script
    // mutates className/style before React attaches, which React would otherwise flag.
    <html lang="en" className="dark" style={{ colorScheme: "dark" }} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
      </head>
      <body
        className={`${sans.variable} ${mono.variable} bg-background text-foreground antialiased`}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

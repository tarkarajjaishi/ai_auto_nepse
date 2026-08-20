import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Poppins } from "next/font/google";

import { Providers } from "@/components/providers";
import { NO_FLASH_SCRIPT } from "@/store/theme";

import "./globals.css";

// Poppins is the default face everywhere — the same family and weights the reference loads
// (Poppins:wght@400;500;600).
//
// IBM Plex Mono stays for the numeric role, and that is not a half-measure. Poppins is a
// geometric sans with proportional figures: "1" is far narrower than "8", so a price column set
// in it re-flows sideways every time a digit changes, and decimal points in a stacked column do
// not line up. Prices, levels and volumes are what this screen exists to compare down a column,
// so they keep a monospaced face. Everything that is prose, a label or a control is Poppins.
//
// Both are self-hosted by next/font rather than linked from Google: a webfont fetched at paint
// time is a flash of fallback on every cold load.
const sans = Poppins({
  variable: "--font-sans",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});
const mono = IBM_Plex_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  weight: ["400", "500", "600"],
});

export const metadata: Metadata = {
  title: { default: "Nepse_ai Terminal", template: "%s · Nepse_ai" },
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

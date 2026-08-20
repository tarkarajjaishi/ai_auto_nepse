import type { Metadata, Viewport } from "next";
import { IBM_Plex_Mono, Instrument_Sans } from "next/font/google";

import { Providers } from "@/components/providers";
import { NO_FLASH_SCRIPT } from "@/store/theme";

import "./globals.css";

// Instrument Sans, read off the reference terminal's own computed style — its --font-sans is
// "Instrument Sans", not the Poppins its marketing and login pages preload. Matching the app
// meant matching the app's face, not the brochure's.
//
// IBM Plex Mono stays for the numeric role, and that is not a half-measure: a proportional face
// makes "1" far narrower than "8", so a price column set in one re-flows sideways every time a
// digit changes and stacked decimal points do not line up. The reference does the same — its
// CHANGE/BID/ASK values are IBM Plex Mono while its labels are not.
//
// Both self-hosted by next/font rather than linked from Google: a webfont fetched at paint time
// is a flash of fallback on every cold load.
const sans = Instrument_Sans({ variable: "--font-sans", subsets: ["latin"] });
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

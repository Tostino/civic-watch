import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Source_Serif_4 } from "next/font/google";

import { SiteHeader } from "@/components/SiteHeader";
import { PlayerProvider } from "@/components/player/PlayerProvider";
import { siteUrl } from "@/lib/site";
import { Providers } from "./providers";
import "./globals.css";

/* Two families, because the archive has two kinds of truth (R2.2).
 *
 * Source Serif carries the county's published record - agendas, minutes,
 * official titles - and reads as a document. Inter carries everything this
 * archive inferred: the transcript, speaker names, and all UI chrome. A reader
 * can tell them apart before reading a word, which no badge achieves, and the
 * distinction survives greyscale, high contrast and print. */
const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const serif = Source_Serif_4({ subsets: ["latin"], variable: "--font-serif", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono-face", display: "swap" });

const TITLE = "Pasco County meeting record";
const DESCRIPTION =
  "A searchable, citable record of Pasco County government meetings — the county's published " +
  "agendas and minutes, joined to the recordings and to who spoke.";

/* `metadataBase` is what turns every relative URL below - and in every page's
 * own metadata - into the absolute one that a share card, a canonical link
 * and the sitemap all require. Without it those fields are silently dropped
 * or resolved against localhost, which looks identical on the site itself and
 * wrong everywhere a link is pasted. It comes from SITE_URL; see lib/site.ts
 * for why the fallback is deliberately, visibly local. */
export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: { default: TITLE, template: `%s · ${TITLE}` },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: TITLE,
    title: TITLE,
    description: DESCRIPTION,
    url: "/",
    locale: "en_US",
  },
  twitter: { card: "summary", title: TITLE, description: DESCRIPTION },
};

/* Applied before first paint so a dark-mode reader never gets a white flash.
 * System preference is the default and the stored choice overrides it. */
const THEME_BOOT = `(function(){try{var t=localStorage.getItem("theme");
if(t==="light"||t==="dark")document.documentElement.setAttribute("data-theme",t);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body className={`${sans.variable} ${serif.variable} ${mono.variable}`}>
        <a href="#main" className="sr-only">
          Skip to main content
        </a>
        <Providers>
          <PlayerProvider>
            <SiteHeader />
            <main id="main">{children}</main>
          </PlayerProvider>
        </Providers>
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Source_Serif_4 } from "next/font/google";

import { SiteHeader } from "@/components/SiteHeader";
import { PlayerProvider } from "@/components/player/PlayerProvider";
import { siteUrl } from "@/lib/site";
import { Providers } from "./providers";
import "./globals.css";

/*
 *  Two families, because the archive has two kinds of truth.
*/
const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const serif = Source_Serif_4({ subsets: ["latin"], variable: "--font-serif", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono-face", display: "swap" });

/* The name and what it is, kept apart on purpose. BRAND is what a tab says
 * once a reader is deep in the archive ("Search · Pasco Watch") and what a
 * share card signs itself with; it matches the header wordmark and the
 * domain. TITLE is the homepage's own line, where the name alone would tell
 * a first-time visitor nothing. */
const BRAND = "Pasco Watch";
const TITLE = `${BRAND} · The Pasco County meeting record`;
const DESCRIPTION =
  "A searchable, citable record of Pasco County government meetings: the county's published " +
  "agendas and minutes, joined to the recordings and to who spoke.";

/* `metadataBase` is what turns every relative URL below - and in every page's
 * own metadata - into the absolute one that a share card, a canonical link
 * and the sitemap all require. Without it those fields are silently dropped
 * or resolved against localhost, which looks identical on the site itself and
 * wrong everywhere a link is pasted. It comes from SITE_URL; see lib/site.ts
 * for why the fallback is deliberately, visibly local. */
export const metadata: Metadata = {
  metadataBase: new URL(siteUrl()),
  title: { default: TITLE, template: `%s · ${BRAND}` },
  description: DESCRIPTION,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: BRAND,
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
    /* The font variables go on <html>, NOT on <body>, and the whole site's
       typography turns on it. next/font declares `--font-sans`,
       `--font-serif` and `--font-mono-face` on whatever element carries these
       classes. app/tokens.css then builds `--font-ui`, `--font-record` and
       `--font-mono` out of them ON `:root`. With the classes on <body>, the
       three tokens on :root referenced variables that did not exist there,
       which makes them invalid at computed-value time - not "fall back to the
       next font in the list", but EMPTY - and every descendant inherited the
       empty value. Every `font-family: var(--font-record)` in the app
       resolved to nothing and the browser used its default serif.
       Symptom: the entire archive rendered in Times New Roman, in both
       themes, while the colour tokens beside it worked perfectly, because
       those depend on nothing outside :root. */
    <html
      lang="en"
      data-scroll-behavior="smooth"
      suppressHydrationWarning
      className={`${sans.variable} ${serif.variable} ${mono.variable}`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT }} />
      </head>
      <body>
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

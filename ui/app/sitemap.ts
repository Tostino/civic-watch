import type { MetadataRoute } from "next";

import { getMeetings } from "@/lib/api";
import { siteUrl } from "@/lib/site";

/**
 * Every meeting, plus the four entry points. A meeting page links to its
 * items and its cases, so a crawler reaches the long tail by following the
 * record rather than by us enumerating twenty thousand URLs here.
 *
 * `lastModified` is the MEETING DATE, not today. A sitemap that stamps
 * everything with the build time tells a crawler that eight years of settled
 * public record changed this morning, which is both false and the fastest way
 * to have the whole file ignored.
 *
 * Written to survive the API being down: a sitemap is not worth failing a
 * build or a request over, and four URLs is a working sitemap.
 */
export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const base = siteUrl();
  const fixed: MetadataRoute.Sitemap = [
    { url: base, changeFrequency: "daily", priority: 1 },
    { url: `${base}/search`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${base}/ask`, changeFrequency: "weekly", priority: 0.8 },
    { url: `${base}/about`, changeFrequency: "monthly", priority: 0.5 },
  ];

  // The endpoint caps `limit` at 500 (web/server.py), so this pages rather
  // than asking for everything and silently getting the first 500 - which is
  // exactly what a sitemap of 504 URLs for a 1,251-meeting archive looked
  // like, and nothing about the output said it had been truncated.
  const PAGE = 500;
  const meetings: { id: number; date: string | null }[] = [];
  try {
    // `when: "all"` deliberately: a meeting scheduled for next month is a
    // published agenda and a real page.
    for (let offset = 0; ; offset += PAGE) {
      const res = await getMeetings({ when: "all", limit: PAGE, offset });
      meetings.push(...res.meetings);
      if (meetings.length >= res.total || res.meetings.length < PAGE) break;
      // The sitemap format allows 50,000 URLs in one file; stop well before
      // producing one no crawler will read rather than loop for ever on an
      // endpoint that has started disagreeing with its own `total`.
      if (meetings.length >= 45_000) break;
    }
  } catch {
    return meetings.length ? [...fixed, ...asUrls(meetings, base)] : fixed;
  }

  return [...fixed, ...asUrls(meetings, base)];
}

function asUrls(
  meetings: { id: number; date: string | null }[],
  base: string,
): MetadataRoute.Sitemap {
  return meetings.map((m) => ({
    url: `${base}/meeting/${m.id}`,
    lastModified: m.date ? new Date(`${m.date}T00:00:00Z`) : undefined,
    changeFrequency: "yearly" as const,
    priority: 0.6,
  }));
}

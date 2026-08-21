import type { MetadataRoute } from "next";
import { allPosts, allTags, CATEGORIES, SITE } from "./blogs/blog";

/* Every indexable URL in one place. Category and tag pages are included on
   purpose: they are the hubs that make the topic tree legible to a crawler,
   and without them each article is an orphan reachable only from the index. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: SITE, priority: 1 },
    { url: `${SITE}/blogs`, priority: 0.9 },
    ...CATEGORIES.map((c) => ({ url: `${SITE}/blogs/category/${c.slug}`, priority: 0.7 })),
    ...allTags().map((t) => ({ url: `${SITE}/blogs/tag/${t.slug}`, priority: 0.5 })),
    ...allPosts().map((p) => ({ url: `${SITE}/blogs/${p.slug}`, priority: 0.8 })),
  ];
}

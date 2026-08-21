import type { Metadata } from "next";
import Link from "next/link";
import { allPosts, CATEGORIES, SITE } from "./blog";
import { Shell, PostCard } from "./ui";
import { Pager, pageSlice, PAGE_SIZE } from "./pager";

export const metadata: Metadata = {
  title: "NEPSE Blog — AI, Data and Nepal Stock Market Analysis | NEPSE Quantum",
  description:
    "In-depth guides on the Nepal Stock Exchange: how NEPSE works, how to analyse NEPSE stocks, and how AI, quantitative finance and quantum computing are changing Nepali investing.",
  alternates: { canonical: `${SITE}/blogs` },
  openGraph: {
    type: "website",
    title: "NEPSE Blog — AI, Data and Nepal Stock Market Analysis",
    description:
      "In-depth guides on NEPSE, AI-powered stock analysis, quantitative finance and the future of investing in Nepal.",
    url: `${SITE}/blogs`,
  },
};

export default async function BlogIndex({
  searchParams,
}: {
  searchParams: Promise<{ category?: string; page?: string }>;
}) {
  const sp = await searchParams;
  const category = CATEGORIES.some((c) => c.slug === sp.category) ? sp.category : undefined;
  const page = Math.max(1, Number(sp.page) || 1);
  return <BlogPage page={page} category={category} />;
}

/* Shared by /blogs and /blogs/page/[page] so the two cannot drift apart. */
export function BlogPage({ page, category }: { page: number; category?: string }) {
  const all = allPosts();
  const posts = category ? all.filter((p) => p.category === category) : all;
  const shown = pageSlice(posts, page);

  /* The ItemList covers THIS page's 16, not all 83: a list claiming items the
     page does not contain is a mismatch between markup and content. */
  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    name: "NEPSE Quantum Blog",
    url: category ? `${SITE}/blogs/category/${category}` : page === 1 ? `${SITE}/blogs` : `${SITE}/blogs/page/${page}`,
    mainEntity: {
      "@type": "ItemList",
      numberOfItems: shown.length,
      itemListElement: shown.map((p, i) => ({
        "@type": "ListItem",
        position: (page - 1) * PAGE_SIZE + i + 1,
        url: `${SITE}/blogs/${p.slug}`,
        name: p.title,
      })),
    },
  };

  return (
    <Shell title="The NEPSE Quantum Blog" fixed>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      {/* filters the grid on THIS page rather than routing away: the selected
          chip is the state, the query param makes it linkable, and the static
          /blogs/category/<slug> hubs stay the canonical version for search. */}
      <div className="chipwrap">
        <Link href="/blogs" className={`chip${category ? "" : " chip-on"}`}>
          All
          <span className="chip-n">{all.length}</span>
        </Link>
        {CATEGORIES.map((c) => {
          const n = all.filter((p) => p.category === c.slug).length;
          return (
            <Link
              key={c.slug}
              href={`/blogs?category=${c.slug}`}
              className={`chip${c.slug === category ? " chip-on" : ""}`}
              aria-current={c.slug === category ? "true" : undefined}
            >
              {c.name}
              <span className="chip-n">{n}</span>
            </Link>
          );
        })}
      </div>

      <div className="grid">
        {shown.map((p) => (
          <PostCard key={p.slug} post={p} />
        ))}
      </div>

      <Pager page={page} total={posts.length} category={category} />
    </Shell>
  );
}

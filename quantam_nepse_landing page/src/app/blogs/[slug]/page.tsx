import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import {
  allPosts,
  AUTHOR,
  categoryName,
  postBySlug,
  SITE,
  tagSlug,
} from "../blog";
import { Prose } from "../prose";
import { Shell, ArticleAside, RelatedAside } from "../ui";

export function generateStaticParams() {
  return allPosts().map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = postBySlug(slug);
  if (!post) return {};
  const url = `${SITE}/blogs/${post.slug}`;
  return {
    title: `${post.title} | NEPSE Quantum`,
    description: post.description,
    keywords: [...post.tags, categoryName(post.category), "NEPSE", "Nepal Stock Exchange"],
    alternates: { canonical: url },
    authors: [{ name: AUTHOR.name, url: AUTHOR.url }],
    openGraph: {
      type: "article",
      title: post.title,
      description: post.description,
      url,
      authors: [AUTHOR.name],
      tags: post.tags,
    },
    twitter: { card: "summary_large_image", title: post.title, description: post.description },
  };
}

export default async function Article({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const post = postBySlug(slug);
  if (!post) notFound();

  const url = `${SITE}/blogs/${post.slug}`;
  const related = allPosts()
    .filter((p) => p.slug !== post.slug && p.category === post.category)
    .slice(0, 5);

  /* Article + BreadcrumbList. No datePublished anywhere — the brief was no
     dates, and a fabricated one is worse than none: Google reads it, and a
     wrong date is a wrong signal it will keep showing in results. */
  const jsonLd = [
    {
      "@context": "https://schema.org",
      "@type": "Article",
      headline: post.title,
      description: post.description,
      articleSection: categoryName(post.category),
      keywords: post.tags.join(", "),
      wordCount: post.words,
      inLanguage: "en",
      mainEntityOfPage: { "@type": "WebPage", "@id": url },
      author: { "@type": "Person", name: AUTHOR.name, url: AUTHOR.url, sameAs: [AUTHOR.url] },
      publisher: { "@type": "Organization", name: "NEPSE Quantum", url: SITE },
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Home", item: SITE },
        { "@type": "ListItem", position: 2, name: "Blog", item: `${SITE}/blogs` },
        {
          "@type": "ListItem",
          position: 3,
          name: categoryName(post.category),
          item: `${SITE}/blogs/category/${post.category}`,
        },
        { "@type": "ListItem", position: 4, name: post.title, item: url },
      ],
    },
  ];

  return (
    <Shell title={post.title} hideTitle fixed>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <div className="withaside">
        <ArticleAside post={post} />

        <div className="asidebody">
          <h1 className="page-h art-h">{post.title}</h1>

      <div className="byline">
        <span>
          By{" "}
          <a href={AUTHOR.url} target="_blank" rel="noopener noreferrer author me" className="link">
            {AUTHOR.name}
          </a>
        </span>
        <span aria-hidden="true">·</span>
        <span>{Math.max(1, Math.round(post.words / 200))} min read</span>
        <span aria-hidden="true">·</span>
        <Link href={`/blogs/category/${post.category}`} className="link">
          {categoryName(post.category)}
        </Link>
      </div>

          {/* only this scrolls: the rails, the heading and the byline stay put,
              so the reader keeps the contents list and the title in view for the
              whole article */}
          <div className="artscroll">
            <p className="lead">{post.description}</p>

            <article>
              <Prose blocks={post.blocks} />
            </article>
          </div>

        </div>

        <RelatedAside heading={`More in ${categoryName(post.category)}`} posts={related} />
      </div>

    </Shell>
  );
}

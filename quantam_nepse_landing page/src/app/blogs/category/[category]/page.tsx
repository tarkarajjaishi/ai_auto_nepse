import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { allPosts, CATEGORIES, categoryName, SITE } from "../../blog";
import { Shell, PostCard } from "../../ui";

export function generateStaticParams() {
  return CATEGORIES.map((c) => ({ category: c.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ category: string }>;
}): Promise<Metadata> {
  const { category } = await params;
  const name = categoryName(category);
  return {
    title: `${name} — NEPSE Quantum Blog`,
    description: `Articles on ${name}: research, guides and analysis of the Nepal Stock Exchange from NEPSE Quantum.`,
    alternates: { canonical: `${SITE}/blogs/category/${category}` },
    openGraph: { type: "website", title: `${name} — NEPSE Quantum`, url: `${SITE}/blogs/category/${category}` },
  };
}

export default async function CategoryPage({
  params,
}: {
  params: Promise<{ category: string }>;
}) {
  const { category } = await params;
  if (!CATEGORIES.some((c) => c.slug === category)) notFound();

  const name = categoryName(category);
  const posts = allPosts().filter((p) => p.category === category);

  return (
    <Shell
      title={name}
      lead={`Every NEPSE Quantum article filed under ${name}.`}
    >
      <div className="grid">
        {posts.map((p) => (
          <PostCard key={p.slug} post={p} />
        ))}
      </div>
      {posts.length === 0 && <p className="lead">Articles in this category are on the way.</p>}
    </Shell>
  );
}

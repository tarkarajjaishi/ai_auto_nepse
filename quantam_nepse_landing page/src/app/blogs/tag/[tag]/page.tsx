import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { allPosts, allTags, SITE, tagSlug } from "../../blog";
import { Shell, PostCard } from "../../ui";

export function generateStaticParams() {
  return allTags().map((t) => ({ tag: t.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ tag: string }>;
}): Promise<Metadata> {
  const { tag } = await params;
  const label = allTags().find((t) => t.slug === tag)?.tag ?? tag;
  return {
    title: `${label} — NEPSE Quantum Blog`,
    description: `NEPSE Quantum articles tagged ${label}.`,
    alternates: { canonical: `${SITE}/blogs/tag/${tag}` },
  };
}

export default async function TagPage({ params }: { params: Promise<{ tag: string }> }) {
  const { tag } = await params;
  const label = allTags().find((t) => t.slug === tag)?.tag;
  if (!label) notFound();

  const posts = allPosts().filter((p) => p.tags.some((t) => tagSlug(t) === tag));

  return (
    <Shell
      title={label}
      lead={`Articles tagged ${label}.`}
    >
      <div className="grid">
        {posts.map((p) => (
          <PostCard key={p.slug} post={p} />
        ))}
      </div>
    </Shell>
  );
}

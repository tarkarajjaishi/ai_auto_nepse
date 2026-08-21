import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { allPosts, SITE } from "../../blog";
import { pageCount } from "../../pager";
import { BlogPage } from "../../page";

export function generateStaticParams() {
  const last = pageCount(allPosts().length);
  /* page 1 lives at /blogs, so this route starts at 2 — generating it here too
     would give the same 16 articles two URLs and split their signals. */
  return Array.from({ length: Math.max(0, last - 1) }, (_, i) => ({ page: String(i + 2) }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ page: string }>;
}): Promise<Metadata> {
  const { page } = await params;
  return {
    title: `NEPSE Blog — page ${page} | NEPSE Quantum`,
    description: `Page ${page} of the NEPSE Quantum article library on NEPSE, AI and quantitative research.`,
    alternates: { canonical: `${SITE}/blogs/page/${page}` },
  };
}

export default async function PagedBlog({ params }: { params: Promise<{ page: string }> }) {
  const { page } = await params;
  const n = Number(page);
  if (!Number.isInteger(n) || n < 2 || n > pageCount(allPosts().length)) notFound();
  return <BlogPage page={n} />;
}

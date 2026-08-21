import type { ReactNode } from "react";
import Link from "next/link";
import { House } from "lucide-react";
import { BackButton } from "./back";
import { headingId, headings, tagSlug, type Post } from "./blog";

/* Shared chrome for every blog route. Server-rendered apart from BackButton,
   which is the one client island under /blogs (browser history has no HTML
   equivalent). Everything a crawler needs — headings, links, article text — is
   still in the static HTML; the island only adds a history call. */
export function Shell({
  title,
  lead,
  fixed,
  hideTitle,
  children,
}: {
  title: string;
  lead?: string;
  /* listing pages lock to one screen; article pages must still scroll */
  fixed?: boolean;
  /* an article renders its own heading inside the centre column so it aligns
     with the body; spanning the full page put it above the left rail instead */
  hideTitle?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`blogwrap${fixed ? " blogwrap-fixed" : ""}`}>
      <main className="page">
        {/* one row: the blog has no nav bar, so the back button doubles as the
            header, and putting the title beside it buys a whole line of height
            back for the grid */}
        <div className="head">
          {/* two separate controls: Back walks the history, Home is a real link
              to the landing page. They are not interchangeable — one is where
              you came from, the other is a fixed destination. */}
          <div className="navbtns">
            <BackButton />
            <Link href="/" className="backbtn" aria-label="Go to home page">
              <House width={16} height={16} strokeWidth={2} aria-hidden="true" />
              Home
            </Link>
          </div>
          {!hideTitle && <h1 className="page-h">{title}</h1>}
        </div>

        {lead && <p className="lead">{lead}</p>}

        {children}
      </main>
    </div>
  );
}

/* Title and summary only. The card carries no tags, read time or chart: with
   four rows on one screen every extra line costs height the titles need, and
   the category chips above the grid already do the filtering the tags did. */
export function PostCard({ post }: { post: Post }) {
  return (
    <article className="card">
      <header className="card-h">
        <span className="card-ico" aria-hidden="true" />
        <h3 className="card-t">
          <Link href={`/blogs/${post.slug}`}>{post.title}</Link>
        </h3>
      </header>
      <p className="card-x">{post.description}</p>
    </article>
  );
}

/* The category rail. Server-rendered from the same source as the index, so the
   counts cannot drift, and every entry is a real crawlable link — this doubles
   as the internal linking that ties each article back into its topic hub. */
/* Contents + tags, on the opposite rail. The contents list is built from the
   article's own h2 blocks, so it can never fall out of step with the page, and
   each entry is a real #anchor — jump links a crawler can see, not scroll JS. */
export function ArticleAside({ post }: { post: Post }) {
  const heads = headings(post.blocks);
  return (
    <div className="aside aside-r">
      {heads.length > 0 && (
        <nav aria-label="On this page">
          <h2 className="aside-h">On this page</h2>
          <ul className="asidelist">
            {heads.map((h) => (
              <li key={h}>
                <Link href={`#${headingId(h)}`} className="asidelink asidelink-toc">
                  {h}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}

      {post.tags.length > 0 && (
        <nav aria-label="Tags" className="aside-tags">
          <h2 className="aside-h">Tags</h2>
          <div className="chipwrap">
            {post.tags.map((t) => (
              <Link key={t} href={`/blogs/tag/${tagSlug(t)}`} className="chip">
                {t}
              </Link>
            ))}
          </div>
        </nav>
      )}
    </div>
  );
}

/* Related reading, as a rail rather than a row under the article. Stacked
   title + summary instead of the grid card: at 320px a four-column card would
   wrap its title to six lines and stop scanning as a list. */
export function RelatedAside({ heading, posts }: { heading: string; posts: Post[] }) {
  if (!posts.length) return null;
  return (
    <aside className="aside aside-rel" aria-label={heading}>
      <h2 className="aside-h">{heading}</h2>
      <ul className="rellist">
        {posts.map((p) => (
          <li key={p.slug}>
            <Link href={`/blogs/${p.slug}`} className="rellink">
              <span className="rellink-t">{p.title}</span>
              <span className="rellink-x">{p.description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}

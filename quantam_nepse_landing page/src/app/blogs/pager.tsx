import Link from "next/link";

export const PAGE_SIZE = 8; /* a 4 x 2 grid, one screen, no scrolling */

export function pageCount(total: number): number {
  return Math.max(1, Math.ceil(total / PAGE_SIZE));
}

export function pageSlice<T>(items: T[], page: number): T[] {
  return items.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
}

/* Route-based paging, not a client-side slice: every page is its own URL with
   its own static HTML, so all 80 articles stay crawlable. A JS-only pager would
   hide 64 of them from a crawler that does not run scripts. */
export function Pager({
  page,
  total,
  category,
}: {
  page: number;
  total: number;
  category?: string;
}) {
  const last = pageCount(total);
  if (last < 2) return null;
  /* filtered views page by query param so the filter survives; the unfiltered
     view keeps its /blogs/page/N paths, which are the static, indexable ones */
  const href = (n: number) =>
    category
      ? `/blogs?category=${category}${n > 1 ? `&page=${n}` : ""}`
      : n === 1
        ? "/blogs"
        : `/blogs/page/${n}`;

  return (
    <nav className="pager" aria-label="Pagination">
      <Link
        href={href(Math.max(1, page - 1))}
        className={`pg ${page === 1 ? "pg-off" : ""}`}
        aria-disabled={page === 1}
      >
        Previous
      </Link>

      {Array.from({ length: last }, (_, i) => i + 1).map((n) => (
        <Link
          key={n}
          href={href(n)}
          className={`pg ${n === page ? "pg-on" : ""}`}
          aria-current={n === page ? "page" : undefined}
        >
          {n}
        </Link>
      ))}

      <Link
        href={href(Math.min(last, page + 1))}
        className={`pg ${page === last ? "pg-off" : ""}`}
        aria-disabled={page === last}
      >
        Next
      </Link>
    </nav>
  );
}

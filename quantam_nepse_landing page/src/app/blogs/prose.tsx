import type { ReactNode } from "react";
import Link from "next/link";
import { headingId, type Block } from "./blog";

/* Inline formatting for the article subset: **bold** and [text](/link).
   Split on both patterns at once so a link inside bold, or the reverse, cannot
   produce overlapping matches. */
function inline(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)]+)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;

  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) {
      out.push(<strong key={`${key}-b${i}`}>{m[1]}</strong>);
    } else {
      const href = m[3];
      const label = m[2];
      out.push(
        href.startsWith("/") ? (
          <Link key={`${key}-l${i}`} href={href} className="link">
            {label}
          </Link>
        ) : (
          <a
            key={`${key}-l${i}`}
            href={href}
            className="link"
            target="_blank"
            rel="noopener noreferrer"
          >
            {label}
          </a>
        ),
      );
    }
    last = m.index + m[0].length;
    i += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Prose({ blocks }: { blocks: Block[] }) {
  return (
    <div className="prose">
      {blocks.map((b, i) => {
        const key = `b${i}`;
        /* a switch, not a chain of ifs: the list variant's discriminant is
           itself a union ("ul" | "ol"), which TS will not narrow away through
           successive early returns */
        switch (b.kind) {
          case "h2":
            return (
              <h2 key={key} id={headingId(b.text)}>
                {inline(b.text, key)}
              </h2>
            );
          case "h3":
            return <h3 key={key}>{inline(b.text, key)}</h3>;
          case "quote":
            return <blockquote key={key}>{inline(b.text, key)}</blockquote>;
          case "ul":
            return (
              <ul key={key}>
                {b.items.map((t, j) => (
                  <li key={`${key}-${j}`}>{inline(t, `${key}-${j}`)}</li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={key}>
                {b.items.map((t, j) => (
                  <li key={`${key}-${j}`}>{inline(t, `${key}-${j}`)}</li>
                ))}
              </ol>
            );
          default:
            return <p key={key}>{inline(b.text, key)}</p>;
        }
      })}
    </div>
  );
}

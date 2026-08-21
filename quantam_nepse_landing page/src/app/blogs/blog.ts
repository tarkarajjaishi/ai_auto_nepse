import fs from "node:fs";
import path from "node:path";

/* Articles are plain .txt files under content/blog — the project stores data as
   .txt and nothing else, and this is data. Each file is a small header, a `---`
   line, then the body. Nothing here runs in the browser: every page that calls
   it is a server component, so the fs read happens at build time and the routes
   ship as static HTML, which is what Google indexes. */

export const AUTHOR = {
  name: "Tarka Raj Jaishi",
  url: "https://www.facebook.com/TarkarajJaishi/",
} as const;

export const SITE = "https://ai.tarkarajjaishi.com.np";

/* The taxonomy. One category per article, chosen from THIS list — a free-text
   category field would fragment into near-duplicates ("AI & NEPSE" vs "AI and
   NEPSE") and split the topical authority the tree is meant to build. */
export const CATEGORIES = [
  { slug: "nepse", name: "NEPSE" },
  { slug: "nepse-today", name: "NEPSE Today" },
  { slug: "nepse-analysis", name: "NEPSE Analysis" },
  { slug: "nepse-stocks", name: "NEPSE Stocks" },
  { slug: "companies", name: "Companies" },
  { slug: "sectors", name: "Sectors" },
  { slug: "ai-and-nepse", name: "AI & NEPSE" },
  { slug: "quantitative-finance", name: "Quantitative Finance" },
  { slug: "quantum-finance", name: "Quantum Finance" },
  { slug: "investment-education", name: "Investment Education" },
  { slug: "market-news", name: "Market News" },
  { slug: "data-and-research", name: "Data & Research" },
] as const;

export type CategorySlug = (typeof CATEGORIES)[number]["slug"];

export function categoryName(slug: string): string {
  return CATEGORIES.find((c) => c.slug === slug)?.name ?? slug;
}

export function tagSlug(tag: string): string {
  return tag
    .toLowerCase()
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

/* Heading anchors. The renderer and the contents rail MUST derive ids the same
   way, or every link in the rail points at nothing — hence one exported helper
   rather than a copy in each file. */
export function headingId(text: string): string {
  return text
    .toLowerCase()
    .replace(/\*\*/g, "")
    .replace(/&/g, "and")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export type Block =
  | { kind: "h2" | "h3" | "p" | "quote"; text: string }
  | { kind: "ul" | "ol"; items: string[] };

/* Section headings, for the contents rail. A `.filter()` does NOT narrow this
   union — the list variant's discriminant is itself a union — so the guard has
   to be a plain `if`, which does. */
export function headings(blocks: Block[]): string[] {
  const out: string[] = [];
  for (const b of blocks) if (b.kind === "h2") out.push(b.text.replace(/\*\*/g, ""));
  return out;
}

export type Post = {
  slug: string;
  title: string;
  description: string;
  category: string;
  tags: string[];
  blocks: Block[];
  words: number;
};

const DIR = path.join(process.cwd(), "content", "blog");

/* A deliberately small block parser. The stack list has no markdown renderer and
   adding one for headings, lists and links would be a dependency for ~40 lines,
   so the article files use this subset and nothing else:

     ## / ###   headings
     -          bullet
     1.         numbered
     >          pull quote
     blank line separates blocks
     **bold** and [text](/link) inline, handled at render time */
function parseBody(src: string): Block[] {
  const blocks: Block[] = [];
  let para: string[] = [];
  let list: { kind: "ul" | "ol"; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) {
      blocks.push({ kind: "p", text: para.join(" ") });
      para = [];
    }
  };
  const flushList = () => {
    if (list) {
      blocks.push(list);
      list = null;
    }
  };

  for (const raw of src.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      flushPara();
      flushList();
      continue;
    }
    if (line.startsWith("### ")) {
      flushPara();
      flushList();
      blocks.push({ kind: "h3", text: line.slice(4) });
    } else if (line.startsWith("## ")) {
      flushPara();
      flushList();
      blocks.push({ kind: "h2", text: line.slice(3) });
    } else if (line.startsWith("> ")) {
      flushPara();
      flushList();
      blocks.push({ kind: "quote", text: line.slice(2) });
    } else if (line.startsWith("- ")) {
      flushPara();
      if (list?.kind !== "ul") {
        flushList();
        list = { kind: "ul", items: [] };
      }
      list.items.push(line.slice(2));
    } else if (/^\d+\.\s/.test(line)) {
      flushPara();
      if (list?.kind !== "ol") {
        flushList();
        list = { kind: "ol", items: [] };
      }
      list.items.push(line.replace(/^\d+\.\s/, ""));
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

function parse(file: string, raw: string): Post {
  const split = raw.indexOf("\n---");
  const head = split === -1 ? "" : raw.slice(0, split);
  const body = split === -1 ? raw : raw.slice(split + 4);

  const field = (name: string) =>
    (head.match(new RegExp(`^${name}:\\s*(.+)$`, "m"))?.[1] ?? "").trim();

  const blocks = parseBody(body);
  const words = blocks.reduce(
    (n, b) => n + ("text" in b ? b.text : b.items.join(" ")).split(/\s+/).length,
    0,
  );

  return {
    slug: field("slug") || file.replace(/\.txt$/, ""),
    title: field("title"),
    description: field("description"),
    category: field("category"),
    tags: field("tags")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
    blocks,
    words,
  };
}

export function allPosts(): Post[] {
  if (!fs.existsSync(DIR)) return [];
  return fs
    .readdirSync(DIR)
    .filter((f) => f.endsWith(".txt"))
    .map((f) => parse(f, fs.readFileSync(path.join(DIR, f), "utf8")))
    .filter((p) => p.title && p.slug)
    .sort((a, b) => a.title.localeCompare(b.title));
}

export function postBySlug(slug: string): Post | undefined {
  return allPosts().find((p) => p.slug === slug);
}

export function allTags(): { tag: string; slug: string; count: number }[] {
  const seen = new Map<string, { tag: string; slug: string; count: number }>();
  for (const p of allPosts()) {
    for (const t of p.tags) {
      const s = tagSlug(t);
      const hit = seen.get(s);
      if (hit) hit.count += 1;
      else seen.set(s, { tag: t, slug: s, count: 1 });
    }
  }
  return [...seen.values()].sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));
}

import { redirect } from "next/navigation";

/**
 * `/admin` is the chart terminal.
 *
 * It used to be the Overview dashboard, but the chart is what the terminal is actually for —
 * landing on a summary you immediately click past is a wasted screen. Overview keeps its own
 * route at `/admin/overview` and its own nav entry, so nothing is lost; only the front door moved.
 *
 * A redirect rather than rendering the chart here, so there is exactly ONE chart route. Rendering
 * it in both places would mean two URLs for the same view, and the symbol/timeframe query params
 * the chart reads would silently do nothing on this one.
 */
export default function AdminIndexPage() {
  redirect("/admin/chart");
}

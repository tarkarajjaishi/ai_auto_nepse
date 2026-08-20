import { redirect } from "next/navigation";

/** The public landing page comes later. Until then `/` is the terminal. */
export default function Home() {
  redirect("/admin");
}

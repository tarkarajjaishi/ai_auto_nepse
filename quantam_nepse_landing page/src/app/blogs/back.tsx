"use client";

import { useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

/* The only client component under /blogs. Genuine "back" means browser history,
   and there is no HTML element for that — a link pointing one level up would be
   a different thing wearing the same label.

   It is a <button>, not a link, because it has no href a crawler could follow;
   Home next to it is the real navigable route. Falls back to the blog index when
   there is no history to go back to (a tab opened straight onto this URL). */
export function BackButton() {
  const router = useRouter();
  return (
    <button
      type="button"
      className="backbtn"
      onClick={() => {
        if (window.history.length > 1) router.back();
        else router.push("/blogs");
      }}
    >
      <ArrowLeft width={16} height={16} strokeWidth={2} aria-hidden="true" />
      Back
    </button>
  );
}

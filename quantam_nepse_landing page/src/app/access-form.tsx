"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Loader2, Search, X } from "lucide-react";

/* The access request form.

   A client component, and the second one on this page after <Reveal>. page.tsx stays a server
   component — the sanctioned shape here is a small leaf that owns its own state, not a
   "use client" at the top of 1,200 lines of static markup.

   It is a native [popover]. That is not a stylistic choice: the top layer is the only place a
   dialog on this page can live without fighting something. .stage clips, every card carries
   overflow-hidden, .glass creates a backdrop-filter containing block, .drift sets will-change,
   and the mobile stylesheet rewrites `position` on .stage's children with !important. A popover
   escapes all of it, needs no z-index, and closes on Escape and on an outside click with no
   JavaScript of ours.

   The element is mounted OUTSIDE <main className="stage">, so no `.stage > …` rule can select
   it in the first place. */

/* Dial codes. Nepal first because that is who this page is for, then the rest of the audience:
   the diaspora, the region, and the places NEPSE-adjacent money actually comes from. Sorted by
   likelihood rather than alphabetically — a Nepali user should not scroll past Andorra. */
const DIAL = [
  ["+977", "Nepal"],
  ["+91", "India"],
  ["+1", "USA / Canada"],
  ["+44", "United Kingdom"],
  ["+61", "Australia"],
  ["+971", "UAE"],
  ["+966", "Saudi Arabia"],
  ["+974", "Qatar"],
  ["+965", "Kuwait"],
  ["+973", "Bahrain"],
  ["+968", "Oman"],
  ["+82", "South Korea"],
  ["+81", "Japan"],
  ["+60", "Malaysia"],
  ["+65", "Singapore"],
  ["+86", "China"],
  ["+852", "Hong Kong"],
  ["+880", "Bangladesh"],
  ["+975", "Bhutan"],
  ["+94", "Sri Lanka"],
  ["+92", "Pakistan"],
  ["+49", "Germany"],
  ["+33", "France"],
  ["+39", "Italy"],
  ["+34", "Spain"],
  ["+351", "Portugal"],
  ["+31", "Netherlands"],
  ["+32", "Belgium"],
  ["+41", "Switzerland"],
  ["+43", "Austria"],
  ["+46", "Sweden"],
  ["+47", "Norway"],
  ["+45", "Denmark"],
  ["+358", "Finland"],
  ["+353", "Ireland"],
  ["+48", "Poland"],
  ["+7", "Russia / Kazakhstan"],
  ["+90", "Turkey"],
  ["+972", "Israel"],
  ["+27", "South Africa"],
  ["+234", "Nigeria"],
  ["+254", "Kenya"],
  ["+20", "Egypt"],
  ["+55", "Brazil"],
  ["+52", "Mexico"],
  ["+54", "Argentina"],
  ["+64", "New Zealand"],
  ["+63", "Philippines"],
  ["+62", "Indonesia"],
  ["+66", "Thailand"],
  ["+84", "Vietnam"],
] as const;

/* These MUST agree with api/access.py, character for character. They are a convenience and not
   a control — the POST can be sent without ever loading this page, which is why the server
   validates independently. But a value the BROWSER accepts and the SERVER refuses is the worst
   outcome of the two: the button is enabled, the person presses it, and the form fails with a
   message they cannot act on and no way to find the offending character.

   That is not hypothetical. "K" followed by a ZERO WIDTH SPACE is two characters to
   String.trim() and contains a letter, so the old check enabled the button — while the server
   strips the invisible and sees a one-character name. Hence `clean()` below: the same removal,
   the same collapse, applied before the same rules. */
const MAX = { full_name: 80, place: 80, email: 254, whatsapp: 20 };

const EMAIL =
  /^[^@\s]{1,64}@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$/;
const SEP = /[\s\-().]/g;
const LETTER = /\p{L}/u;
/* [0-9], never \d. Python's \d is Unicode-aware and matched Devanagari digits — which is what a
   Nepali keyboard produces — so the two sides disagreed about exactly the audience this page is
   written for. JS's \d is already ASCII-only; spelling it out keeps the two files comparable. */
const DIGITS = /^[0-9]{6,15}$/;

/* Mirrors _clean() in api/access.py: drop invisible formatting, keep the two joiners that are
   real orthography in Devanagari (ZWNJ/ZWJ — they choose between a conjunct and a half-form, so
   removing them misspells a Nepali name), collapse runs of whitespace, trim. */
const INVISIBLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/gu;
function clean(value: string) {
  return value
    .replace(INVISIBLE, (ch) => (ch === "‌" || ch === "‍" ? ch : ""))
    .replace(/\s+/g, " ")
    .trim();
}

type Fields = {
  full_name: string;
  whatsapp_code: string;
  whatsapp: string;
  place: string;
  email: string;
};

const EMPTY: Fields = {
  full_name: "",
  whatsapp_code: "+977",
  whatsapp: "",
  place: "",
  email: "",
};

function errorsFor(f: Fields): Partial<Record<keyof Fields, string>> {
  const e: Partial<Record<keyof Fields, string>> = {};

  const name = clean(f.full_name);
  if (!name) e.full_name = "Your full name is required.";
  else if (name.length < 2 || !LETTER.test(name)) e.full_name = "Please enter your full name.";
  else if (name.length > MAX.full_name) e.full_name = "That name is too long.";

  const place = clean(f.place);
  if (!place) e.place = "This field is required.";
  else if (place.length < 2 || !LETTER.test(place)) e.place = "Please enter where you live.";
  else if (place.length > MAX.place) e.place = "That location is too long.";

  const email = clean(f.email);
  if (!email) e.email = "An email address is required.";
  else if (email.length > MAX.email) e.email = "That email address is too long.";
  else if (!EMAIL.test(email)) e.email = "That does not look like an email address.";

  for (const [key, label] of [["whatsapp", "WhatsApp number"]] as const) {
    const digits = clean(f[key]).replace(SEP, "");
    if (!digits) e[key] = `Your ${label} is required.`;
    else if (!DIGITS.test(digits)) e[key] = `Please enter a valid ${label}.`;
    // one repeated digit is not a number anyone can ring, and the server refuses it too
    else if (new Set(digits).size === 1) e[key] = `Please enter a valid ${label}.`;
  }
  return e;
}

export function AccessForm() {
  const [f, setF] = useState<Fields>(EMPTY);
  const [touched, setTouched] = useState<Partial<Record<keyof Fields, boolean>>>({});
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const [failed, setFailed] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const done = useRef<HTMLButtonElement>(null);
  /* A ref, not the busy STATE, for the in-flight guard: state is stale inside the handler that
     set it, so a double-click or an Enter-then-click sends the form twice. */
  const inFlight = useRef(false);

  const errors = errorsFor(f);
  const complete = Object.keys(errors).length === 0;

  /* Focus follows the screen. Without this the invoker keeps focus when the panel opens, so a
     keyboard or screen-reader user is given a dialog they are not in; and when the success
     screen replaces the form it unmounts the focused submit button, which drops focus to
     <body> and loses the reader's place entirely. */
  useEffect(() => {
    if (sent) done.current?.focus();
  }, [sent]);

  const set = (k: keyof Fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setF((prev) => ({ ...prev, [k]: e.target.value }));
    setFailed("");
  };
  const blur = (k: keyof Fields) => () => setTouched((t) => ({ ...t, [k]: true }));
  const show = (k: keyof Fields) => (touched[k] ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    /* Belt and braces. The button is disabled until `complete`, but a form is also submitted by
       Enter, and a disabled button is a UI state rather than a guarantee. */
    if (!complete || inFlight.current) {
      setTouched({
        full_name: true, whatsapp: true, place: true, email: true, whatsapp_code: true,
      });
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setFailed("");
    try {
      const res = await fetch("/api/access-request", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          full_name: clean(f.full_name),
          place: clean(f.place),
          email: clean(f.email),
          whatsapp_code: f.whatsapp_code,
          whatsapp: clean(f.whatsapp).replace(SEP, ""),
        }),
      });
      /* Read the body BEFORE branching on ok: the server's 400 carries the sentence meant for
         the person filling the form, and throwing it away leaves "something went wrong". */
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (!res.ok) {
        setFailed(
          body.error ||
            (res.status === 429
              ? "Too many requests just now. Please try again in a few minutes."
              : `Could not send that (${res.status}). Please try again.`),
        );
        return;
      }
      setSent(true);
    } catch {
      setFailed("Could not reach the server. Please check your connection and try again.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  function reset() {
    /* Called when the popover closes. Without it a second visitor on a shared machine — or the
       same person reopening — is greeted by the previous submission's success screen. */
    setF(EMPTY);
    setTouched({});
    setSent(false);
    setFailed("");
    setBusy(false);
    inFlight.current = false;
  }

  return (
    <div
      /* @ts-expect-error -- `popover` is a valid HTML attribute; React 19 passes it through,
         but the DOM typings in this React version do not declare it yet. */
      popover="auto"
      id="access-modal"
      ref={panel}
      className="accessmodal"
      /* role/aria-modal, because a [popover] is a `generic` element to the accessibility tree:
         without them the panel has no role, aria-labelledby is ignored, and a screen reader
         announces nothing at all when it opens. */
      role="dialog"
      aria-modal="true"
      aria-labelledby="access-modal-title"
      onToggle={(e) => {
        /* ONLY this element's own toggle. The country-code list is a NESTED popover, and its
           toggle events arrive at this handler too — so picking a country fired the "closed"
           branch here and ran reset(), wiping every field the person had already typed. It
           also fired the "open" branch, which stole focus back to the name input the moment
           the list appeared. Measured both. */
        if (e.target !== e.currentTarget) return;
        const state = (e as unknown as { newState?: string }).newState;
        if (state === "closed") reset();
        if (state === "open") setTimeout(() => first.current?.focus(), 0);
      }}
    >
      {/* popoverTargetAction, NOT an onClick. The TRIGGER that opens this panel is declarative
          HTML and therefore live the moment the markup parses — so the panel can be open before
          this component has hydrated, and until then an onClick handler is not attached and the
          close button does nothing at all. The user just cannot get out. Expressed this way the
          button is the browser's own popover control: no JavaScript of ours is involved, so it
          works in that window and it works if the bundle never arrives. */}
      <button
        type="button"
        className="ax-close"
        aria-label="Close"
        popoverTarget="access-modal"
        popoverTargetAction="hide"
      >
        <X width={16} height={16} strokeWidth={2} aria-hidden="true" />
      </button>

      {/* The panel pins, this scrolls. When the panel itself was the scroll container the close
          button — absolutely positioned inside it — scrolled away with the content, so on a
          landscape phone there was no visible way out. */}
      <div className="ax-body">
        {sent ? (
          <div className="ax-done">
            <span className="ax-tick" aria-hidden="true">
              <Check width={26} height={26} strokeWidth={3} />
            </span>
            <h2 id="access-modal-title" className="ax-h">
              Sent successfully
            </h2>
            {/* aria-live so the change is announced: the heading swap alone is silent */}
            <p className="ax-sub" role="status">
              You will be contacted soon.
            </p>
            <button
              type="button"
              ref={done}
              className="ax-submit"
              popoverTarget="access-modal"
              popoverTargetAction="hide"
            >
              Done
            </button>
          </div>
        ) : (
          <form onSubmit={submit} noValidate>
            <h2 id="access-modal-title" className="ax-h">
              Request Access
            </h2>
            <p className="ax-sub">
              Nepse Quantam is invite-only while we onboard. Tell us how to reach you and we
              will be in touch.
            </p>

            <Field id="ax-name" label="Full Name" error={show("full_name")}>
              {(p) => (
                <input
                  {...p}
                  ref={first}
                  className="ax-in"
                  value={f.full_name}
                  onChange={set("full_name")}
                  onBlur={blur("full_name")}
                  autoComplete="name"
                  placeholder="Your full name"
                  maxLength={MAX.full_name}
                />
              )}
            </Field>

            {/* WhatsApp only. A separate phone number was asked for and removed: for this
                audience the WhatsApp number IS the phone number, so the second field cost a
                row of the form without adding a way to reach anybody. */}
            <Field id="ax-wa" label="WhatsApp Number" error={show("whatsapp")}>
              {(p) => (
                <div className="ax-tel">
                  <DialSelect
                    value={f.whatsapp_code}
                    onChange={(code) => setF((prev) => ({ ...prev, whatsapp_code: code }))}
                  />
                  <input
                    {...p}
                    aria-label="WhatsApp number"
                    className="ax-in"
                    value={f.whatsapp}
                    onChange={set("whatsapp")}
                    onBlur={blur("whatsapp")}
                    inputMode="tel"
                    placeholder="9801234567"
                    maxLength={MAX.whatsapp}
                  />
                </div>
              )}
            </Field>

            <Field id="ax-place" label="Currently Living In" error={show("place")}>
              {(p) => (
                <input
                  {...p}
                  className="ax-in"
                  value={f.place}
                  onChange={set("place")}
                  onBlur={blur("place")}
                  autoComplete="address-level2"
                  placeholder="City, Country"
                  maxLength={MAX.place}
                />
              )}
            </Field>

            <Field id="ax-email" label="Email Address" error={show("email")}>
              {(p) => (
                <input
                  {...p}
                  className="ax-in"
                  type="email"
                  value={f.email}
                  onChange={set("email")}
                  onBlur={blur("email")}
                  autoComplete="email"
                  placeholder="you@example.com"
                  maxLength={MAX.email}
                />
              )}
            </Field>

            {failed && (
              <p className="ax-failed" role="alert">
                {failed}
              </p>
            )}

            <button
              type="submit"
              className="ax-submit"
              disabled={!complete || busy}
              aria-describedby="ax-note"
            >
              {busy && <Loader2 className="ax-spin" width={15} height={15} aria-hidden="true" />}
              {busy ? "Sending…" : "Request Access"}
            </button>

            {/* Says WHY the button is dead, and is tied to it by aria-describedby so a screen
                reader gets the reason rather than just "dimmed". */}
            <p className="ax-note" id="ax-note">
              {complete
                ? "We only use these details to contact you about access."
                : "Fill in every field to continue."}
            </p>
          </form>
        )}
      </div>
    </div>
  );
}

/**
 * The dial-code picker.
 *
 * A native <select> cannot do what this needs: it renders the SELECTED OPTION'S OWN TEXT when
 * closed, so showing "+977" in a control narrow enough to sit beside the number and "+977 Nepal"
 * in the open list is not expressible. Either the closed control reads "+977 Nep…" or the list
 * is 51 bare numbers nobody outside Nepal can choose from.
 *
 * So it is a listbox. Two decisions in it are load-bearing:
 *
 *   It is a NESTED [popover]. The list has to escape `.ax-body`, which is `overflow-y: auto` —
 *   an absolutely positioned list inside it would be cut off at the scroller's edge. A popover
 *   goes to the top layer, and one opened from inside another popover is NESTED, so opening it
 *   does not light-dismiss the dialog behind it and Escape closes only this.
 *
 *   It is positioned by script, not by CSS. Anchor positioning would do it declaratively but is
 *   not in every browser this page serves yet, and a top-layer element has no useful containing
 *   block to fall back on. The trigger's rect is read on open, and the list flips above it when
 *   there is not room below.
 */
function DialSelect({ value, onChange }: { value: string; onChange: (code: string) => void }) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);

  const needle = q.trim().toLowerCase();
  const list = needle
    ? DIAL.filter(([code, name]) => name.toLowerCase().includes(needle) || code.includes(needle))
    : DIAL;

  function place() {
    const b = btn.current, p = pop.current;
    if (!b || !p) return;
    const r = b.getBoundingClientRect();
    /* 265, not the trigger's own width: the trigger is a narrow code-only control, and the
       list has to fit "+44  United Kingdom" — the whole reason this is not a <select>. Capped
       to the viewport so a small phone still gets a list that fits on screen. */
    const width = Math.min(Math.max(r.width, 265), window.innerWidth - 16);
    const room = window.innerHeight - r.bottom;
    /* flip up when the trigger is near the bottom — on a landscape phone the dialog itself
       nearly fills the screen, so "below the trigger" is regularly off-screen */
    const height = Math.min(280, window.innerHeight - 24);
    p.style.width = `${width}px`;
    p.style.maxHeight = `${height}px`;
    p.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - width - 8))}px`;
    p.style.top = room > height + 8 ? `${r.bottom + 4}px` : `${Math.max(8, r.top - height - 4)}px`;
  }

  function choose(code: string) {
    onChange(code);
    pop.current?.hidePopover();
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => {
        const next = e.key === "ArrowDown" ? i + 1 : i - 1;
        return Math.max(0, Math.min(list.length - 1, next));
      });
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (list[active]) choose(list[active][0]);
    }
  }

  return (
    <>
      <button
        type="button"
        ref={btn}
        className="ax-cc"
        popoverTarget="ax-dial"
        aria-label={`Country code, currently ${value}`}
        aria-haspopup="listbox"
      >
        <span>{value}</span>
        <ChevronDown width={13} height={13} strokeWidth={2} aria-hidden="true" />
      </button>

      <div
        /* @ts-expect-error -- see the note on the dialog itself: React 19 passes `popover`
           through but the DOM typings in this version do not declare it. */
        popover="auto"
        id="ax-dial"
        ref={pop}
        className="ax-dial"
        onToggle={(e) => {
          if (e.target !== e.currentTarget) return;
          const state = (e as unknown as { newState?: string }).newState;
          if (state === "open") {
            place();
            setQ("");
            setActive(Math.max(0, DIAL.findIndex(([c]) => c === value)));
            setTimeout(() => search.current?.focus(), 0);
          }
        }}
      >
        <div className="ax-dial-search">
          <Search width={13} height={13} strokeWidth={2} aria-hidden="true" />
          <input
            ref={search}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
            }}
            onKeyDown={onKey}
            placeholder="Search country or code"
            aria-label="Search country or dial code"
            aria-controls="ax-dial-list"
          />
        </div>

        <ul className="ax-dial-list" id="ax-dial-list" role="listbox" aria-label="Country code">
          {list.map(([code, name], i) => (
            <li key={`${code}${name}`}>
              <button
                type="button"
                role="option"
                aria-selected={code === value}
                className={`ax-dial-opt${i === active ? " ax-dial-on" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(code)}
              >
                <span className="ax-dial-code">{code}</span>
                <span className="ax-dial-name">{name}</span>
                {code === value && <Check width={13} height={13} strokeWidth={3} aria-hidden="true" />}
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="ax-dial-none">No country matches that.</li>}
        </ul>
      </div>
    </>
  );
}

/**
 * One labelled field.
 *
 * The error sits OUTSIDE the <label> and is wired with aria-describedby. Inside it, the message
 * became part of the input's accessible NAME — a screen reader read "Full Name Your full name is
 * required" as the field's name — and nothing marked the field invalid.
 */
function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  children: (props: {
    id: string;
    "aria-invalid": boolean | undefined;
    "aria-describedby": string | undefined;
  }) => React.ReactNode;
}) {
  const errId = `${id}-err`;
  return (
    <div className={`ax-field${error ? " ax-bad" : ""}`}>
      <label className="ax-label" htmlFor={id}>
        {label}
      </label>
      {children({
        id,
        "aria-invalid": error ? true : undefined,
        "aria-describedby": error ? errId : undefined,
      })}
      {/* The slot is always rendered so the panel does not jump as messages appear. */}
      <span className="ax-err" id={errId}>
        {error ?? ""}
      </span>
    </div>
  );
}

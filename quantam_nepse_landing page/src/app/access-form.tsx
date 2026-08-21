"use client";

import { useRef, useState } from "react";
import { Check, Loader2, X } from "lucide-react";

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

/* These MUST mirror api/access.py. They are a convenience, not a control — the POST can be sent
   without ever loading this page, which is why the server validates independently. Keeping the
   two in step matters for a different reason: a field the browser accepts and the server
   rejects is a form that fails on submit with no way for the user to see why. */
const EMAIL = /^[^@\s]{1,64}@[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$/;
const SEP = /[\s\-().]/g;
const LETTER = /\p{L}/u;

type Fields = {
  full_name: string;
  country_code: string;
  phone: string;
  whatsapp_code: string;
  whatsapp: string;
  place: string;
  email: string;
};

const EMPTY: Fields = {
  full_name: "",
  country_code: "+977",
  phone: "",
  whatsapp_code: "+977",
  whatsapp: "",
  place: "",
  email: "",
};

function errorsFor(f: Fields): Partial<Record<keyof Fields, string>> {
  const e: Partial<Record<keyof Fields, string>> = {};
  const name = f.full_name.trim();
  if (!name) e.full_name = "Your full name is required.";
  else if (name.length < 2 || !LETTER.test(name)) e.full_name = "Please enter your full name.";

  const place = f.place.trim();
  if (!place) e.place = "This field is required.";
  else if (place.length < 2 || !LETTER.test(place)) e.place = "Please enter where you live.";

  const email = f.email.trim();
  if (!email) e.email = "An email address is required.";
  else if (!EMAIL.test(email)) e.email = "That does not look like an email address.";

  for (const [key, label] of [
    ["phone", "phone number"],
    ["whatsapp", "WhatsApp number"],
  ] as const) {
    const digits = f[key].replace(SEP, "");
    if (!digits) e[key] = `Your ${label} is required.`;
    else if (!/^\d{6,15}$/.test(digits)) e[key] = `Please enter a valid ${label}.`;
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

  const errors = errorsFor(f);
  const complete = Object.keys(errors).length === 0;

  const set = (k: keyof Fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setF((prev) => ({ ...prev, [k]: e.target.value }));
    setFailed("");
  };
  const blur = (k: keyof Fields) => () => setTouched((t) => ({ ...t, [k]: true }));
  const show = (k: keyof Fields) => (touched[k] ? errors[k] : undefined);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    /* Belt and braces. The button is disabled until `complete`, but a form can also be
       submitted with Enter, and a disabled button is a UI state rather than a guarantee. */
    if (!complete || busy) {
      setTouched({
        full_name: true, phone: true, whatsapp: true, place: true, email: true,
        country_code: true, whatsapp_code: true,
      });
      return;
    }
    setBusy(true);
    setFailed("");
    try {
      const res = await fetch("/api/access-request", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          ...f,
          full_name: f.full_name.trim(),
          place: f.place.trim(),
          email: f.email.trim(),
          phone: f.phone.replace(SEP, ""),
          whatsapp: f.whatsapp.replace(SEP, ""),
        }),
      });
      /* Read the body BEFORE branching on ok: the server's 400 carries the sentence meant for
         the person filling the form, and throwing it away leaves "something went wrong". */
      const body = await res.json().catch(() => ({}) as { error?: string });
      if (!res.ok) {
        setFailed(body.error || `Could not send that (${res.status}). Please try again.`);
        return;
      }
      setSent(true);
    } catch {
      setFailed("Could not reach the server. Please check your connection and try again.");
    } finally {
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
  }

  return (
    <div
      /* @ts-expect-error -- `popover` is a valid HTML attribute; React 19 passes it through,
         but the DOM typings in this React version do not declare it yet. */
      popover="auto"
      id="access-modal"
      ref={panel}
      className="accessmodal"
      onToggle={(e) => {
        if ((e as unknown as { newState?: string }).newState === "closed") reset();
      }}
      aria-labelledby="access-modal-title"
    >
      <button
        type="button"
        className="ax-close"
        aria-label="Close"
        onClick={() => panel.current?.hidePopover()}
      >
        <X width={16} height={16} strokeWidth={2} aria-hidden="true" />
      </button>

      {sent ? (
        <div className="ax-done" role="status">
          <span className="ax-tick" aria-hidden="true">
            <Check width={26} height={26} strokeWidth={3} />
          </span>
          <h2 id="access-modal-title" className="ax-h">
            Sent successfully
          </h2>
          <p className="ax-sub">You will be contacted soon.</p>
          <button
            type="button"
            className="ax-submit"
            onClick={() => panel.current?.hidePopover()}
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
            Nepse Quantam is invite-only while we onboard. Tell us how to reach you and we will
            be in touch.
          </p>

          <Field label="Full Name" error={show("full_name")}>
            <input
              className="ax-in"
              value={f.full_name}
              onChange={set("full_name")}
              onBlur={blur("full_name")}
              autoComplete="name"
              placeholder="Your full name"
              maxLength={80}
            />
          </Field>

          <Field label="Phone Number" error={show("phone")}>
            <div className="ax-tel">
              <select
                className="ax-cc"
                value={f.country_code}
                onChange={set("country_code")}
                aria-label="Phone country code"
              >
                {DIAL.map(([code, name]) => (
                  <option key={`p${code}${name}`} value={code}>
                    {code} {name}
                  </option>
                ))}
              </select>
              <input
                className="ax-in"
                value={f.phone}
                onChange={set("phone")}
                onBlur={blur("phone")}
                inputMode="tel"
                autoComplete="tel-national"
                placeholder="9801234567"
                maxLength={20}
              />
            </div>
          </Field>

          <Field label="WhatsApp Number" error={show("whatsapp")}>
            <div className="ax-tel">
              <select
                className="ax-cc"
                value={f.whatsapp_code}
                onChange={set("whatsapp_code")}
                aria-label="WhatsApp country code"
              >
                {DIAL.map(([code, name]) => (
                  <option key={`w${code}${name}`} value={code}>
                    {code} {name}
                  </option>
                ))}
              </select>
              <input
                className="ax-in"
                value={f.whatsapp}
                onChange={set("whatsapp")}
                onBlur={blur("whatsapp")}
                inputMode="tel"
                placeholder="9801234567"
                maxLength={20}
              />
            </div>
          </Field>

          <Field label="Currently Living In" error={show("place")}>
            <input
              className="ax-in"
              value={f.place}
              onChange={set("place")}
              onBlur={blur("place")}
              autoComplete="address-level2"
              placeholder="City, Country"
              maxLength={80}
            />
          </Field>

          <Field label="Email Address" error={show("email")}>
            <input
              className="ax-in"
              type="email"
              value={f.email}
              onChange={set("email")}
              onBlur={blur("email")}
              autoComplete="email"
              placeholder="you@example.com"
              maxLength={254}
            />
          </Field>

          {failed && (
            <p className="ax-failed" role="alert">
              {failed}
            </p>
          )}

          <button type="submit" className="ax-submit" disabled={!complete || busy}>
            {busy && <Loader2 className="ax-spin" width={15} height={15} aria-hidden="true" />}
            {busy ? "Sending…" : "Request Access"}
          </button>

          {/* Says WHY the button is dead. A disabled control with no explanation is the most
              common way a form silently loses somebody. */}
          <p className="ax-note">
            {complete
              ? "We only use these details to contact you about access."
              : "Fill in every field to continue."}
          </p>
        </form>
      )}
    </div>
  );
}

function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={`ax-field${error ? " ax-bad" : ""}`}>
      <span className="ax-label">{label}</span>
      {children}
      {/* The slot is always rendered so the panel does not jump as messages appear */}
      <span className="ax-err">{error ?? ""}</span>
    </label>
  );
}

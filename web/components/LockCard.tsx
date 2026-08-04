"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { SectionIcon } from "@/components/SectionLabel";
import { getMe, postCheckout, type Me } from "@/lib/api";

// Paid-section placeholder. Rendered for any key in `locked_sections` — which is
// always empty until PAYWALL_ENABLED flips, so before that only the /story route
// and the operator inspector exercise this component. The flip needs no change here.
//
// Design §04: one card standing in for a run of locked sections, naming exactly
// what is behind it. `titles` comes straight from the locked keys, so the card
// always tells the truth about which sections the API withheld.
//
// That truthfulness is enforced on the backend, not here: `gate_sections`
// (graph/sections.py) only names a section in `locked_sections` if it was really
// produced AND non-empty. Before 2026-07-30 the spec's literal
// ["bench_plan","chips","watchlist"] would have made this card advertise a `chips`
// section no writer emitted. Do not "helpfully" pad `titles` from a static list.
//
// The prices below are COPY, not inputs. POST /api/checkout takes a plan NAME and
// resolves the price server-side (api/billing.price_id_for_plan), so editing these
// strings in devtools changes what the card says and nothing about what is charged.
export default function LockCard({
  titles,
  teamId,
}: {
  titles: string[];
  teamId?: number | null;
}) {
  const [email, setEmail] = useState("");
  const [team, setTeam] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [busy, setBusy] = useState<"monthly" | "season" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);

  // Accounts build (2026-08-04): the card adapts to who is reading it, but the
  // LOCK ITSELF is still decided by the API's `locked_sections`. This component
  // never unlocks anything; it only changes what it offers.
  useEffect(() => {
    getMe()
      .then((m) => {
        setMe(m);
        if (m.signed_in && m.email) setEmail(m.email);
      })
      .catch(() => setMe(null));
  }, []);

  const buy = async (plan: "monthly" | "season") => {
    if (!email.includes("@")) {
      setError("Enter the email your briefs should go to.");
      return;
    }
    if (!agreed) {
      setError("Tick the box to agree to the terms first.");
      return;
    }
    setError(null);
    setBusy(plan);
    try {
      const { checkout_url } = await postCheckout(
        plan,
        email,
        teamId ?? (team ? Number(team) : null),
        // Carries the ticked box to the server, which provisions the account with
        // consent recorded. Without it the webhook creates a bare account and this
        // customer's own emailed link would open the free view.
        agreed
      );
      window.location.href = checkout_url;
    } catch {
      // 503 here is our misconfiguration or Paddle's, never the buyer's input —
      // the plan name was validated by a Literal before the request left.
      setError("Checkout is unavailable right now. Try again in a minute.");
      setBusy(null);
    }
  };

  // A PAYING CUSTOMER WHO IS ONE CHECKBOX BEHIND MUST NOT SEE A BUY BUTTON.
  // `entitled` is true and the sections are still locked only when terms are
  // outstanding; offering to sell them what they already own would be the worst
  // moment in the product.
  if (me?.signed_in && me.entitled && me.terms_required) {
    return (
      <section className="mt-6 rounded-[11px] border border-out-red bg-surface-900 p-4 text-center sm:p-5">
        <div className="mb-1.5 flex items-center justify-center gap-[7px] text-body font-semibold text-ink-300">
          <SectionIcon name="locked" />
          <h2 className="m-0 text-body font-semibold">One step to unlock</h2>
        </div>
        <p className="m-0 mb-3.5 text-body leading-relaxed text-ink-600">
          Your subscription is active. We&apos;ve updated our terms since you last
          agreed — accept them and {titles.join(", ")} unlock straight away.
        </p>
        <Link href="/login" className="btn-primary inline-block px-5 no-underline">
          Review and accept
        </Link>
      </section>
    );
  }

  return (
    <section className="mt-6 rounded-[11px] border border-line-strong bg-surface-900 p-4 text-center sm:p-5">
      <div className="mb-1.5 flex items-center justify-center gap-[7px] text-body font-semibold text-ink-300">
        <SectionIcon name="locked" />
        <h2 className="m-0 text-body font-semibold">In the full brief</h2>
      </div>
      <p className="m-0 mb-3.5 text-body leading-relaxed text-ink-600">{titles.join(" · ")}</p>

      <div className="mx-auto flex max-w-[320px] flex-col gap-2">
        <input
          type="email"
          value={email}
          onChange={(ev) => setEmail(ev.target.value)}
          placeholder="you@example.com"
          aria-label="Email for your briefs"
          className="field bg-surface-900 py-2.5 text-body"
        />
        {teamId == null && (
          <input
            type="text"
            inputMode="numeric"
            value={team}
            onChange={(ev) => setTeam(ev.target.value.replace(/\D/g, ""))}
            placeholder="FPL team ID (optional)"
            aria-label="FPL team ID"
            className="field bg-surface-900 py-2.5 text-body"
          />
        )}
        <label className="flex cursor-pointer items-start gap-2 text-left text-eyebrow leading-normal tracking-normal text-ink-600">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(ev) => setAgreed(ev.target.checked)}
            className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-in-green"
          />
          <span>
            I agree to the{" "}
            <Link href="/privacy" className="underline">
              privacy policy
            </Link>{" "}
            and terms.
          </span>
        </label>
        <button
          type="button"
          disabled={busy !== null || !agreed}
          onClick={() => buy("monthly")}
          className="btn-ghost border-line-strong bg-surface-700 px-5 hover:bg-surface-600 disabled:opacity-60"
        >
          {busy === "monthly" ? "Opening checkout…" : "Unlock weekly briefs · $3.99/mo"}
        </button>
        <button
          type="button"
          disabled={busy !== null || !agreed}
          onClick={() => buy("season")}
          className="text-eyebrow leading-normal tracking-normal text-ink-700 underline disabled:opacity-60"
        >
          {busy === "season" ? "Opening checkout…" : "or $19 for the whole season"}
        </button>
      </div>

      {error && <p className="m-0 mt-2.5 text-label text-out-red">{error}</p>}
    </section>
  );
}

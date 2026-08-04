"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import {
  getMe,
  googleSignInUrl,
  postAcceptTerms,
  postRequestLink,
  type Me,
} from "@/lib/api";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

// Sign in (accounts build, 2026-08-04).
//
// THIS PAGE IS NOT ON THE PATH TO A FREE BRIEF. Nothing links here from the
// draft picker or the landing hero's primary flow; it is reachable from the
// header and from a paid lock card. Free readers never need it, and if a
// redirect to /login ever appears in front of `POST /api/brief`, that is the
// design constraint broken, not a routing detail.
//
// Three states, one page:
//   1. signed out          -> magic link + Google, terms checkbox
//   2. signed in, terms ok -> a short "you're signed in" confirmation
//   3. signed in, terms due -> the acceptance panel (first signup OR a version bump)
//
// (3) reuses (1)'s checkbox copy deliberately: a customer re-accepting after a
// terms change should see the same words they agreed to the first time.

const ERRORS: Record<string, string> = {
  link_invalid: "That sign-in link has expired or was already used. Request a new one.",
  state_mismatch: "That sign-in attempt didn't come from this browser. Try again.",
  google_unverified:
    "That Google account's email address isn't verified with Google, so we can't use it to sign in.",
  google_failed: "Google sign-in didn't complete. Try again, or use an email link.",
  google_disabled: "Google sign-in isn't available right now. Use an email link.",
};

function LoginInner() {
  const [me, setMe] = useState<Me | null>(null);
  const [email, setEmail] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [next, setNext] = useState("/");

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const err = q.get("error");
    if (err) setError(ERRORS[err] || "Sign-in didn't complete. Try again.");
    // Only a path is ever kept — the server refuses anything else anyway
    // (accounts.safe_redirect), and mirroring the rule here keeps the UI honest.
    const target = q.get("next") || "/";
    setNext(target.startsWith("/") && !target.startsWith("//") ? target : "/");
    getMe().then(setMe).catch(() => setMe(null));
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!agreed || !email.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await postRequestLink(email.trim(), next);
      // "If that address can sign in" — never "we sent it" and never "no such
      // account". The API answers identically either way and so must this copy.
      setSent(true);
    } catch {
      setError("Couldn't send the link. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  };

  const accept = async () => {
    setBusy(true);
    try {
      await postAcceptTerms();
      setMe(await getMe());
    } catch {
      setError("Couldn't record that. Try again.");
    } finally {
      setBusy(false);
    }
  };

  const terms = (
    <label className="flex cursor-pointer items-start gap-2 text-meta leading-relaxed text-ink-600">
      <input
        type="checkbox"
        checked={agreed}
        onChange={(ev) => setAgreed(ev.target.checked)}
        className="mt-0.5 h-4 w-4 shrink-0 accent-in-green"
      />
      <span>
        I agree to the{" "}
        <Link href="/privacy" className="underline">
          privacy policy
        </Link>{" "}
        and to receiving the emails I ask for. Not affiliated with the Premier League.
      </span>
    </label>
  );

  // --- signed in, terms outstanding ---------------------------------------
  if (me?.signed_in && me.terms_required) {
    return (
      <div className="card p-5 sm:p-8">
        <h1 className="m-0 text-title font-bold text-ink-0">One thing before you go on</h1>
        <p className="mt-2 text-body leading-relaxed text-ink-600">
          {me.entitled
            ? "Your subscription is active. We've updated our terms since you last agreed, so we need your consent again before showing you the paid sections."
            : "We've updated our terms since you last agreed."}
        </p>
        <div className="mt-4">
          <label className="flex cursor-pointer items-start gap-2 text-meta leading-relaxed text-ink-600">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(ev) => setAgreed(ev.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-in-green"
            />
            <span>
              I agree to the{" "}
              <Link href="/privacy" className="underline">
                privacy policy
              </Link>{" "}
              (version {me.terms_version}).
            </span>
          </label>
        </div>
        <button
          type="button"
          disabled={!agreed || busy}
          onClick={accept}
          className="btn-primary mt-4 px-5 disabled:opacity-40"
        >
          {busy ? "Saving…" : "Accept and continue"}
        </button>
        {error && <p className="mt-3 text-meta text-out-red">{error}</p>}
      </div>
    );
  }

  // --- signed in, nothing outstanding -------------------------------------
  if (me?.signed_in) {
    return (
      <div className="card p-5 sm:p-8">
        <h1 className="m-0 text-title font-bold text-ink-0">You&apos;re signed in</h1>
        <p className="mt-2 text-body leading-relaxed text-ink-600">
          As {me.email}.{" "}
          {me.entitled
            ? "Your subscription is active — open a brief and the full report is there."
            : "You're on the free tier. The paid sections unlock when a subscription is active."}
        </p>
        <Link href={next} className="btn-primary mt-4 inline-block px-5 no-underline">
          Continue
        </Link>
      </div>
    );
  }

  // --- signed out ----------------------------------------------------------
  return (
    <div className="card p-5 sm:p-8">
      <h1 className="m-0 text-title font-bold text-ink-0">Sign in</h1>
      <p className="mt-2 text-body leading-relaxed text-ink-600">
        Signing in is only needed to read the sections you pay for. The weekly brief
        stays free and needs no account —{" "}
        <Link href="/" className="underline">
          go straight to it
        </Link>
        .
      </p>

      {sent ? (
        <div className="mt-5 rounded border border-line-strong bg-surface-700 p-4">
          <p className="m-0 text-body font-medium text-ink-0">Check your email</p>
          <p className="mt-1 text-meta leading-relaxed text-ink-600">
            If that address can sign in, a link is on its way. It works once and expires
            in 15 minutes.
          </p>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-5 flex flex-col gap-3">
          <input
            type="email"
            required
            value={email}
            onChange={(ev) => setEmail(ev.target.value)}
            placeholder="you@example.com"
            autoComplete="email"
            className="field"
          />
          {terms}
          <button
            type="submit"
            disabled={!agreed || busy}
            className="btn-primary px-5 disabled:opacity-40"
          >
            {busy ? "Sending…" : "Email me a sign-in link"}
          </button>
        </form>
      )}

      {me?.google_enabled && (
        <>
          <div className="my-5 flex items-center gap-3">
            <span className="h-px flex-1 bg-line-dim" />
            <span className="text-eyebrow font-medium uppercase text-ink-700">or</span>
            <span className="h-px flex-1 bg-line-dim" />
          </div>
          <a
            href={agreed ? googleSignInUrl(next) : undefined}
            aria-disabled={!agreed}
            onClick={(ev) => {
              // The checkbox gates BOTH providers. The server records consent
              // separately (POST /api/auth/accept-terms) and will prompt if this
              // is ever bypassed, so this is the courtesy, not the control.
              if (!agreed) ev.preventDefault();
            }}
            className={`btn-ghost block border-line-strong px-4 text-center no-underline ${
              agreed ? "" : "pointer-events-none opacity-40"
            }`}
          >
            Continue with Google
          </a>
          {!agreed && (
            <p className="mt-2 text-meta text-ink-700">
              Tick the box above to continue with either option.
            </p>
          )}
        </>
      )}

      {error && <p className="mt-4 text-meta text-out-red">{error}</p>}
    </div>
  );
}

export default function LoginPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[560px] flex-1 px-3 py-10 sm:px-6">
        <Suspense fallback={<div className="card p-5 sm:p-8" />}>
          <LoginInner />
        </Suspense>
      </main>
      <SiteFooter />
    </div>
  );
}

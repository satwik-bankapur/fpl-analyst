"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMe, postLogout, type Me } from "@/lib/api";

// The header's account corner (accounts build, 2026-08-04).
//
// RENDERS NOTHING UNTIL `me` RESOLVES, and renders the free experience if the
// call fails. A header that flashed "Sign in" and then swapped to an address
// would be worse than a beat of nothing, and — more importantly — an API that is
// down must not make the site look logged-out-and-broken to a free reader who
// never needed an account in the first place.
//
// This is the ONLY place in the app that asks who you are for chrome purposes.
// Page-level entitlement still comes from the brief response's `locked_sections`,
// never from this component — two sources for "can they read it" is how the two
// disagree.
export function AuthNav() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setReady(true));
  }, []);

  if (!ready) return <span className="h-5 w-16" aria-hidden />;

  if (!me?.signed_in) {
    return (
      <Link
        href="/login"
        className="text-eyebrow font-semibold uppercase text-ink-500 no-underline hover:text-ink-0"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3">
      {me.terms_required && (
        <Link
          href="/login"
          className="text-eyebrow font-semibold uppercase text-out-red no-underline"
        >
          Action needed
        </Link>
      )}
      <span className="hidden text-eyebrow font-medium text-ink-700 sm:inline">
        {me.email}
      </span>
      <Link
        href="/account"
        className="text-eyebrow font-semibold uppercase text-ink-500 no-underline hover:text-ink-0"
      >
        Account
      </Link>
      <button
        type="button"
        onClick={() =>
          postLogout()
            .then(() => window.location.reload())
            .catch(() => window.location.reload())
        }
        className="border-0 bg-transparent p-0 text-eyebrow font-semibold uppercase text-ink-700 hover:text-ink-0"
      >
        Sign out
      </button>
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMe, postDeleteAccount, postLogout, type Me } from "@/lib/api";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

// The signed-in account page (accounts build, 2026-08-04). Small on purpose:
// there is one account, one subscription and one destructive action, and none of
// them needs a dashboard.
//
// THE DELETE BUTTON IS TWO CLICKS AND SAYS WHAT SURVIVES. Deleting an account
// does NOT cancel a Paddle subscription — nothing here can reach into a payment
// provider and stop a card being charged. `subscription_cancel_required` comes
// back from the API for exactly this reason, and the confirmation copy states it
// before the click, not after.
export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ cancelRequired: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setReady(true));
  }, []);

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await postDeleteAccount();
      setDone({ cancelRequired: result.subscription_cancel_required });
    } catch {
      setError("Couldn't delete the account. Try again, or reply to any brief.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[560px] flex-1 px-3 py-10 sm:px-6">
        {!ready && <div className="card p-5 sm:p-8" />}

        {ready && done && (
          <div className="card p-5 sm:p-8">
            <h1 className="m-0 text-title font-bold text-ink-0">Account deleted</h1>
            <p className="mt-2 text-body leading-relaxed text-ink-600">
              Your account, sessions and mailing-list entry are gone. Billing records are
              kept anonymised — we&apos;re required to hold them and they no longer name you.
            </p>
            {done.cancelRequired && (
              <div className="mt-4 rounded border border-out-red bg-out-redbg p-4">
                <p className="m-0 text-body font-semibold text-ink-0">
                  Your subscription is still active
                </p>
                <p className="mt-1 text-meta leading-relaxed text-ink-600">
                  Deleting the account here does not cancel the payment. Cancel it in the
                  Paddle receipt email from your purchase, or reply to any brief and
                  we&apos;ll do it by hand — otherwise the card keeps being charged.
                </p>
              </div>
            )}
            <Link href="/" className="btn-primary mt-4 inline-block px-5 no-underline">
              Back to the brief
            </Link>
          </div>
        )}

        {ready && !done && !me?.signed_in && (
          <div className="card p-5 sm:p-8">
            <h1 className="m-0 text-title font-bold text-ink-0">You&apos;re not signed in</h1>
            <p className="mt-2 text-body leading-relaxed text-ink-600">
              The weekly brief is free and needs no account.{" "}
              <Link href="/login" className="underline">
                Sign in
              </Link>{" "}
              only if you pay for the full report.
            </p>
          </div>
        )}

        {ready && !done && me?.signed_in && (
          <div className="card p-5 sm:p-8">
            <h1 className="m-0 text-title font-bold text-ink-0">Your account</h1>
            <dl className="mt-4 grid grid-cols-[auto,1fr] gap-x-6 gap-y-2 text-body">
              <dt className="text-ink-700">Email</dt>
              <dd className="m-0 text-ink-0">{me.email}</dd>
              <dt className="text-ink-700">Sign-in</dt>
              <dd className="m-0 text-ink-0">
                {me.auth_provider === "google" ? "Google" : "Email link"}
              </dd>
              <dt className="text-ink-700">Subscription</dt>
              <dd className="m-0 text-ink-0">{me.entitled ? "Active" : "Free tier"}</dd>
              <dt className="text-ink-700">Terms</dt>
              <dd className="m-0 text-ink-0">
                {me.terms_required ? (
                  <Link href="/login" className="text-out-red underline">
                    Acceptance needed
                  </Link>
                ) : (
                  `Accepted (${me.terms_version})`
                )}
              </dd>
            </dl>

            <div className="mt-6 flex flex-wrap gap-2">
              <button
                type="button"
                className="btn-ghost px-4"
                onClick={() =>
                  postLogout()
                    .then(() => window.location.assign("/"))
                    .catch(() => window.location.assign("/"))
                }
              >
                Sign out
              </button>
            </div>

            <hr className="my-6 border-line-dim" />

            {!confirming ? (
              <button
                type="button"
                className="btn-ghost border-out-red px-4 text-out-red"
                onClick={() => setConfirming(true)}
              >
                Delete my account
              </button>
            ) : (
              <div className="rounded border border-out-red bg-out-redbg p-4">
                <p className="m-0 text-body font-semibold text-ink-0">
                  Delete this account permanently?
                </p>
                <ul className="mt-2 list-disc pl-5 text-meta leading-relaxed text-ink-600">
                  <li>Your account, every sign-in session and your mailing-list entry go.</li>
                  <li>Billing records are kept, anonymised — we&apos;re required to hold them.</li>
                  {me.entitled && (
                    <li className="text-out-red">
                      This does <strong>not</strong> cancel your subscription. Cancel it
                      from your Paddle receipt, or the card keeps being charged.
                    </li>
                  )}
                </ul>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={remove}
                    className="btn-ghost border-out-red px-4 text-out-red"
                  >
                    {busy ? "Deleting…" : "Yes, delete it"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-4"
                    onClick={() => setConfirming(false)}
                  >
                    Keep my account
                  </button>
                </div>
              </div>
            )}
            {error && <p className="mt-3 text-meta text-out-red">{error}</p>}
          </div>
        )}
      </main>
      <SiteFooter />
    </div>
  );
}

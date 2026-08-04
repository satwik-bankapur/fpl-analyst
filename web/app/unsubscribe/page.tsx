"use client";

import { useEffect, useState } from "react";
import { postMailConsent } from "@/lib/api";
import { SiteHeader, SiteFooter } from "@/components/Chrome";

// The landing for the visible "Unsubscribe" link in every email footer.
//
// This page is the COURTESY path, not the mechanism. The mechanism is the
// `List-Unsubscribe` header, which points straight at POST /api/email/unsubscribe
// so Gmail and Yahoo can act with no human, no JavaScript and no cookie — this
// page could 500 and unsubscribing would still work. Keep it that way.
//
// It unsubscribes ON LOAD rather than asking first: someone who clicked
// "unsubscribe" has already answered the question, and a confirm step is how you
// earn a spam complaint instead of an unsubscribe. Undo is offered afterwards.
export default function UnsubscribePage() {
  const [state, setState] = useState<"working" | "done" | "resubscribed" | "deleted" | "error">(
    "working"
  );
  const [creds, setCreds] = useState<{ e: string; t: string } | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const e = q.get("e") || "";
    const t = q.get("t") || "";
    if (!e || !t) {
      setState("error");
      return;
    }
    setCreds({ e, t });
    postMailConsent("unsubscribe", e, t)
      .then(() => setState("done"))
      .catch(() => setState("error"));
  }, []);

  const act = (action: "resubscribe" | "delete", next: "resubscribed" | "deleted") => {
    if (!creds) return;
    setState("working");
    postMailConsent(action, creds.e, creds.t)
      .then(() => setState(next))
      .catch(() => setState("error"));
  };

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[560px] flex-1 px-3 py-10 sm:px-6">
        <div className="card p-5 sm:p-8">
          {state === "working" && <p className="m-0 text-body text-ink-600">One moment…</p>}

          {state === "error" && (
            <>
              <h1 className="m-0 text-title font-bold text-ink-0">That link didn&apos;t work</h1>
              <p className="mt-2 text-body leading-relaxed text-ink-600">
                It may have been truncated by your mail client. Reply to any brief with
                &ldquo;unsubscribe&rdquo; and we&apos;ll take you off by hand.
              </p>
            </>
          )}

          {state === "done" && (
            <>
              <h1 className="m-0 text-title font-bold text-ink-0">You&apos;re unsubscribed</h1>
              <p className="mt-2 text-body leading-relaxed text-ink-600">
                No more emails to {creds?.e}. If you pay for briefs, your subscription is
                untouched — you can still read them on the site.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn-ghost border-line-strong bg-surface-700 px-4"
                  onClick={() => act("resubscribe", "resubscribed")}
                >
                  Actually, keep sending
                </button>
                <button
                  type="button"
                  className="btn-ghost border-line-strong px-4"
                  onClick={() => act("delete", "deleted")}
                >
                  Delete my address
                </button>
              </div>
            </>
          )}

          {state === "resubscribed" && (
            <>
              <h1 className="m-0 text-title font-bold text-ink-0">You&apos;re back on the list</h1>
              <p className="mt-2 text-body leading-relaxed text-ink-600">
                The next brief lands on Friday.
              </p>
            </>
          )}

          {state === "deleted" && (
            <>
              <h1 className="m-0 text-title font-bold text-ink-0">Deleted</h1>
              <p className="mt-2 text-body leading-relaxed text-ink-600">
                Your address is gone from the mailing list. Billing records are kept
                anonymised — we&apos;re required to hold them, they no longer name you.
              </p>
            </>
          )}
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}

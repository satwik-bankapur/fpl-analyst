"use client";

import { useCallback } from "react";
import { Failed, Loading, PageTitle, useLoad, timeAgo } from "@/components/AdminBits";
import { adminGet, AccountList } from "@/lib/admin";

// Read-only account list (accounts build, 2026-08-04).
//
// NOTHING ON THIS PAGE WRITES. No edit, no "sign in as", no reset — every
// control here is a link or a sort. That is a deliberate ceiling on what the
// admin token is worth: it reads the customer list, it cannot become a customer.
//
// Addresses are shown in full, unlike in logs. The line the audit drew (§D4.3) is
// storage-vs-log, not a blanket ban: this table has one token-gated reader, and
// support cannot answer "why didn't my brief arrive" against a column of hashes.
export default function AdminAccounts() {
  const { data, error, loading, reload } = useLoad<AccountList>(
    useCallback(() => adminGet<AccountList>("/admin/accounts"), []),
    []
  );

  if (error) return <Failed error={error} onRetry={reload} />;
  if (loading || !data) return <Loading what="accounts" />;

  const stale = data.accounts.filter((a) => !a.terms_current).length;

  return (
    <>
      <PageTitle
        right={
          <span className="text-meta text-ink-600">
            {data.count} account{data.count === 1 ? "" : "s"} · terms {data.terms_version}
          </span>
        }
      >
        Accounts
      </PageTitle>

      {stale > 0 && (
        <p className="mb-4 rounded border border-line-strong bg-surface-800 p-3 text-meta text-ink-600">
          <strong className="text-ink-0">{stale}</strong> account
          {stale === 1 ? " has" : "s have"} not accepted the current terms. They see the
          free view until they do, however their subscription reads.
        </p>
      )}

      {data.accounts.length === 0 ? (
        <p className="text-body text-ink-600">No accounts yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-left text-meta">
            <thead>
              <tr className="border-b border-line-strong text-eyebrow uppercase text-ink-700">
                <th className="py-2 pr-3 font-semibold">Email</th>
                <th className="py-2 pr-3 font-semibold">Sign-in</th>
                <th className="py-2 pr-3 font-semibold">Subscription</th>
                <th className="py-2 pr-3 font-semibold">Team</th>
                <th className="py-2 pr-3 font-semibold">Terms</th>
                <th className="py-2 pr-3 font-semibold">Sessions</th>
                <th className="py-2 font-semibold">Last login</th>
              </tr>
            </thead>
            <tbody>
              {data.accounts.map((a) => (
                <tr key={a.id} className="border-b border-line-dim align-top">
                  <td className="py-2 pr-3 text-ink-0">{a.email}</td>
                  <td className="py-2 pr-3 text-ink-600">
                    {a.auth_provider === "google" ? "Google" : "Email link"}
                  </td>
                  <td className="py-2 pr-3">
                    {a.subscription_status ? (
                      <span className={a.entitled ? "text-in-green" : "text-out-red"}>
                        {a.subscription_status}
                        {a.subscription_plan ? ` · ${a.subscription_plan}` : ""}
                      </span>
                    ) : (
                      <span className="text-ink-700">free</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-ink-600">{a.fpl_team_id ?? "—"}</td>
                  <td className="py-2 pr-3">
                    {a.terms_current ? (
                      <span className="text-ink-600">{a.terms_version}</span>
                    ) : (
                      <span className="text-out-red">
                        {a.terms_version ? `${a.terms_version} (stale)` : "never"}
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-ink-600">{a.active_sessions}</td>
                  <td className="py-2 text-ink-600">
                    {a.last_login_at ? timeAgo(a.last_login_at) : "never"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

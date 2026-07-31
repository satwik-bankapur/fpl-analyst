"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Eyebrow } from "@/components/Chrome";
import { UNAUTHORIZED_EVENT, checkToken, clearToken, getToken, setToken } from "@/lib/admin";

const NAV = [
  { href: "/admin", label: "Dashboard" },
  { href: "/admin/notes", label: "Team notes" },
  { href: "/admin/euro-weeks", label: "European weeks" },
  { href: "/admin/fixtures", label: "Fixtures" },
  { href: "/admin/inspect", label: "Inspect" },
];

// Token gate + nav for every /admin page. One prompt, stored locally, re-prompted
// on any 401 from anywhere (lib/admin dispatches UNAUTHORIZED_EVENT), so a rotated
// or mistyped token lands on this form rather than an error page or a crash.
export default function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [token, setTok] = useState("");
  const [ready, setReady] = useState(false);
  const [entry, setEntry] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<"rejected" | "unreachable" | null>(null);

  useEffect(() => {
    setTok(getToken());
    setReady(true);
  }, []);

  useEffect(() => {
    const onKicked = () => {
      setTok("");
      setProblem("rejected");
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onKicked);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onKicked);
  }, []);

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!entry.trim() || busy) return;
      setBusy(true);
      // Verify before storing: a bad token saved silently would make every page
      // on the site look broken instead of pointing at the token.
      const result = await checkToken(entry);
      setBusy(false);
      if (result !== "ok") {
        setProblem(result);
        return;
      }
      setToken(entry);
      setTok(entry.trim());
      setEntry("");
      setProblem(null);
    },
    [entry, busy]
  );

  const forget = () => {
    clearToken();
    setTok("");
    setProblem(null);
  };

  if (!ready) return null; // localStorage is client-only; don't flash the form

  if (!token) {
    return (
      <main className="mx-auto flex min-h-screen max-w-[420px] flex-col justify-center px-5">
        <Eyebrow tone={problem ? "error" : "muted"}>
          {problem === "rejected" ? "Token rejected" : problem ? "API unreachable" : "Admin"}
        </Eyebrow>
        <h1 className="mb-2 mt-3 text-title-lg font-bold">Enter the admin token.</h1>
        <p className="m-0 mb-5 text-body leading-relaxed text-ink-600">
          {problem === "rejected"
            ? "That token isn't valid for this API. Check ADMIN_TOKEN on the server you're pointed at."
            : problem === "unreachable"
              ? "The API didn't answer. Check it's running and that FRONTEND_ORIGIN matches the address in your URL bar — your token may be fine."
              : "The value of ADMIN_TOKEN on the API. Stored in this browser only."}
        </p>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <input
            type="password"
            autoFocus
            value={entry}
            onChange={(e) => setEntry(e.target.value)}
            placeholder="ADMIN_TOKEN"
            aria-label="Admin token"
            className="field"
          />
          <button type="submit" disabled={!entry.trim() || busy} className="btn-primary">
            {busy ? "Checking…" : "Unlock"}
          </button>
        </form>
      </main>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line-dim">
        <div className="mx-auto flex max-w-[1180px] flex-wrap items-center gap-x-5 gap-y-2 px-5 py-3 sm:px-8">
          <span className="text-[15px] font-bold tracking-[-0.01em]">Scout Report admin</span>
          <nav className="flex flex-wrap gap-x-4 gap-y-1">
            {NAV.map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={`text-body font-medium no-underline ${
                  pathname === n.href ? "text-ink-0" : "text-ink-600 hover:text-ink-300"
                }`}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <button
            type="button"
            onClick={forget}
            className="ml-auto text-eyebrow font-semibold uppercase text-ink-700 hover:text-ink-500"
          >
            Forget token
          </button>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-7 sm:px-8">{children}</main>
    </div>
  );
}

"use client";

import { useState } from "react";
import { postSignup } from "@/lib/api";

// Inline email row from the design: field + short button, no labels above.
// `source` tags which surface captured the address (landing hero vs brief page).
export default function EmailCapture({
  teamId,
  label,
  source = "landing",
}: {
  teamId?: number | null;
  label?: string;
  source?: string;
}) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (done) return <p className="m-0 text-body text-in-green">Thanks — check your inbox.</p>;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await postSignup(email, teamId, source);
      setDone(true);
    } catch {
      setError("That didn't work — check the address.");
    }
  }

  return (
    <form onSubmit={submit} noValidate={false}>
      <div className="flex gap-2">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@email.com"
          aria-label="Email address"
          className="field min-w-0 flex-1 bg-surface-900 py-2.5 text-body"
        />
        <button className="btn-primary shrink-0 whitespace-nowrap px-3.5 py-2.5 text-label">
          {label || "Send it"}
        </button>
      </div>
      {error && (
        <p className="m-0 mt-2 text-body text-out-red" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

"use client";

import { useState } from "react";
import { postSignup } from "@/lib/api";

export default function EmailCapture({ teamId, label }: { teamId?: number | null; label?: string }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (done) return <p className="text-sm text-pitch">Thanks — check your inbox.</p>;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await postSignup(email, teamId);
      setDone(true);
    } catch {
      setError("That didn't work — check the address.");
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 sm:flex-row">
      <input
        type="email"
        required
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="you@email.com"
        className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
      />
      <button className="rounded-md bg-ink px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {label || "Get the free weekly preview"}
      </button>
      {error && <span className="text-sm text-red-600">{error}</span>}
    </form>
  );
}

import Link from "next/link";

export const dynamic = "force-dynamic";

// Season brief route (§2.2). Stage A generates no season briefs and mints no
// tokens, so this is a pre-season placeholder; in Stage B this fetches
// GET /api/brief/{teamId}/{gw}?token= and renders BriefView (with locked cards
// until checkout). Kept so the route + share links exist from day one.
export default function TeamBriefPage({ params }: { params: { teamId: string } }) {
  return (
    <main className="mx-auto max-w-2xl px-6 py-24 text-center">
      <h1 className="text-2xl font-bold">Season briefs start at GW1</h1>
      <p className="mt-3 text-slate-600">
        Team {params.teamId} isn&apos;t playing yet — it&apos;s pre-season. Rate your draft
        instead and we&apos;ll have your GW1 brief ready when the season opens.
      </p>
      <Link href="/" className="mt-6 inline-block rounded-lg bg-pitch px-6 py-3 font-semibold text-white">
        Rate my draft
      </Link>
    </main>
  );
}

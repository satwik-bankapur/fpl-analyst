import { SiteHeader, SiteFooter } from "@/components/Chrome";
import NotFoundState from "@/components/NotFoundState";

export const dynamic = "force-dynamic";

// Season brief route (§2.2). Stage A generates no season briefs and mints no
// tokens, so this is a pre-season placeholder; in Stage B this fetches
// GET /api/brief/{teamId}/{gw} and renders BriefView (with locked cards until
// checkout). Kept so the route + share links exist from day one.
//
// The `?token=` query param the spec shows is NOT accepted by the API today — it
// was removed on 2026-07-30 rather than left accepted-and-ignored, since nothing
// mints or verifies a JWT yet. Stage B adds it back with the send job.
//
// Styled as the design's "no team" state (§03) because that is what it is today:
// a team ID that resolves to no brief, with the one route that still works.
export default function TeamBriefPage({ params }: { params: { teamId: string } }) {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-[560px] flex-1 flex-col">
        <NotFoundState
          kicker={`No brief yet · ID ${params.teamId}`}
          title="Season briefs start at GW1."
          hint={
            <>
              Your team ID is the number in the URL when you open <em>Points</em> on the FPL site:{" "}
              <br />
              fantasy.premierleague.com/entry/
              <span className="font-bold text-ink-0">{params.teamId}</span>/event/1
            </>
          }
          cta={{ href: "/", label: "Rate my draft instead" }}
        >
          <p className="m-0">
            It&apos;s pre-season, so team {params.teamId} has no gameweek to be scouted on yet. Rate your draft
            now and the GW1 brief will be ready when the season opens.
          </p>
        </NotFoundState>
      </main>
      <SiteFooter />
    </div>
  );
}

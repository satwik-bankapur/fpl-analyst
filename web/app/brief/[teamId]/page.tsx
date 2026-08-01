import { getHealth, getTeamBrief, Brief } from "@/lib/api";
import BriefView from "@/components/BriefView";
import EmailCapture from "@/components/EmailCapture";
import { SiteHeader, SiteFooter } from "@/components/Chrome";
import NotFoundState from "@/components/NotFoundState";

export const dynamic = "force-dynamic";

// Season brief route (§2.2), and the destination of every tokenized link the send
// job mails out: /brief/{teamId}?gw=N&token=<jwt>.
//
// The token is passed straight through to the API and verified THERE (api/tokens
// plus a live entitlement re-check). Nothing here inspects or trusts it — a page
// that decided for itself whether a token was good would be a second, weaker
// paywall, and the one in graph/sections.py is the only one allowed to exist.
//
// No token, a stale token or an expired one is not an error case: the API answers
// with the free sections and a populated `locked_sections`, and the lock cards do
// the explaining. That is why there is no auth branch below.
//
// (2026-08-01: this replaced the Stage A placeholder, whose comment said `?token=`
// was not accepted by the API. Stage B restored the parameter.)
async function load(teamId: number, gw: number, token?: string): Promise<Brief | null> {
  try {
    return await getTeamBrief(teamId, gw, token);
  } catch {
    return null;
  }
}

async function resolveGw(raw?: string): Promise<number | null> {
  const asked = Number(raw);
  if (Number.isInteger(asked) && asked >= 1 && asked <= 38) return asked;
  try {
    return (await getHealth()).next_gw;
  } catch {
    return null;
  }
}

export default async function TeamBriefPage({
  params,
  searchParams,
}: {
  params: { teamId: string };
  searchParams: { gw?: string; token?: string };
}) {
  const teamId = Number(params.teamId);
  const gw = Number.isInteger(teamId) && teamId > 0 ? await resolveGw(searchParams.gw) : null;
  const brief = gw ? await load(teamId, gw, searchParams.token) : null;

  if (brief) {
    return (
      <div className="flex min-h-screen flex-col">
        <SiteHeader />
        <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-3 sm:px-6 sm:py-8">
          <BriefView brief={brief} teamId={teamId} />
          {brief.locked_sections.length === 0 && (
            <div className="card mt-3.5 p-5 sm:p-6">
              <div className="mb-2.5 text-[15px] font-semibold">Want this every Friday?</div>
              <EmailCapture source="team_brief" />
            </div>
          )}
        </main>
        <SiteFooter />
      </div>
    );
  }

  // Pre-season, an unknown ID, and a team with no picks yet all land here. The API
  // does not distinguish them to an anonymous caller and neither should this page:
  // a different message per case is a team-ID enumeration oracle.
  // Styled as the design's "no team" state (§03).
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

import { SiteHeader, SiteFooter } from "@/components/Chrome";

// PLACEHOLDER — routing and the footer link exist so the prose is the only thing
// left to write. Paddle is the merchant of record and will not approve a live
// account without a reachable refund policy (docs/LAUNCH-CHECKLIST.md).
export const metadata = { title: "Refunds — The Scout Report" };

export default function RefundsPage() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto w-full max-w-[840px] flex-1 px-3 py-8 sm:px-6">
        <article className="card p-5 sm:p-8">
          <h1 className="m-0 text-title font-bold sm:text-title-lg">Refunds</h1>

          <div className="mt-5 max-w-measure space-y-4 text-prose text-ink-100 sm:text-prose-lg">
            <p className="m-0">
              TODO — write the refund policy. Cover at minimum: the window (state a
              number of days), how a refund is requested, how the monthly plan differs
              from the one-off season pass, what happens to access after a refund, and
              that Paddle is the merchant of record so refunds are issued through Paddle.
            </p>
          </div>
        </article>
      </main>
      <SiteFooter />
    </div>
  );
}

import { SiteHeader, SiteFooter } from "@/components/Chrome";
import NotFoundState from "@/components/NotFoundState";

export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main className="mx-auto flex w-full max-w-[560px] flex-1 flex-col">
        <NotFoundState kicker="404 · no page here" title="There's nothing at this address.">
          <p className="m-0">
            The link may be mistyped, or it may point at something that hasn&apos;t been built yet.
          </p>
        </NotFoundState>
      </main>
      <SiteFooter />
    </div>
  );
}

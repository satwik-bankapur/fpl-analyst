import Link from "next/link";

// Shared page chrome from the design: a thin bordered top bar and a legal
// footer. Both appear on every screen in the mockups, so they live in one file.
export function SiteHeader({ right }: { right?: React.ReactNode }) {
  return (
    <header className="border-b border-line-dim">
      <div className="mx-auto flex max-w-[1180px] items-center justify-between px-5 py-3 sm:px-12 sm:py-3.5">
        <Link
          href="/"
          className="text-[15px] font-bold tracking-[-0.01em] text-ink-0 no-underline hover:text-ink-0 sm:text-[17px]"
        >
          The Scout Report
        </Link>
        {right}
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="mt-auto border-t border-line-dim">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-2 px-5 py-5 sm:flex-row-reverse sm:items-center sm:justify-between sm:px-12 sm:py-6">
        <div className="flex gap-5">
          <a href="mailto:hello@thescoutreport.fpl" className="text-eyebrow font-medium tracking-normal text-ink-500">
            Contact
          </a>
          <Link href="/privacy" className="text-eyebrow font-medium tracking-normal text-ink-500">
            Privacy
          </Link>
        </div>
        <p className="m-0 text-eyebrow leading-normal tracking-normal text-ink-700">
          Not affiliated with the Premier League or Fantasy Premier League.
        </p>
      </div>
    </footer>
  );
}

/** Uppercase kicker above a heading (design §05 "eyebrow"). */
export function Eyebrow({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "error" }) {
  return (
    <div
      className={`text-eyebrow font-semibold uppercase ${tone === "error" ? "text-out-red" : "text-ink-600"}`}
    >
      {children}
    </div>
  );
}

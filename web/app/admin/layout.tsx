import type { Metadata } from "next";
import AdminShell from "@/components/AdminShell";

// /admin is operator-only. It is never linked from a public page (deliberately —
// grep the repo before adding one) and must never be indexed.
export const metadata: Metadata = {
  title: "Admin — The Scout Report",
  robots: { index: false, follow: false, nocache: true },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <AdminShell>{children}</AdminShell>;
}

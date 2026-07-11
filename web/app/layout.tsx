import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FPL Analyst — your team, scouted",
  description: "Personalized Fantasy Premier League scout reports: verdict, transfer, captaincy — free.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

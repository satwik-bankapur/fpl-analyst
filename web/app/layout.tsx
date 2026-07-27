import type { Metadata } from "next";
import { Instrument_Sans } from "next/font/google";
import "./globals.css";

// One family carries everything — headings, prose, labels, numbers, buttons
// (design §05). next/font self-hosts it, so no external font request at runtime.
const sans = Instrument_Sans({ subsets: ["latin"], display: "swap", variable: "--font-sans" });

export const metadata: Metadata = {
  title: "The Scout Report — your personal FPL analyst",
  description: "Personalized Fantasy Premier League scout reports: verdict, transfer, captaincy — free.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sans.className}>
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import { Instrument_Sans, Newsreader } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const instrument = Instrument_Sans({ variable: "--font-instrument", subsets: ["latin"] });
const newsreader = Newsreader({ variable: "--font-newsreader", subsets: ["latin"], style: ["normal", "italic"] });

export const metadata: Metadata = {
  title: "Deep Synth",
  description: "Agents research a technical question, check each other's sources, and write a cited brief.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${instrument.variable} ${newsreader.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">
        <header className="mx-auto w-full max-w-6xl px-4 sm:px-6 pt-5 pb-2 flex items-baseline gap-3">
          <Link href="/" className="font-semibold tracking-tight text-ink text-[17px]">
            Deep Synth
          </Link>
          <span className="text-ink-faint text-sm hidden sm:inline">research briefs with checked sources</span>
        </header>
        <main className="mx-auto w-full max-w-6xl px-4 sm:px-6 pb-20 flex-1">{children}</main>
      </body>
    </html>
  );
}

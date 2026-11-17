import "./globals.css";
import type { Metadata } from "next";
import { Nav } from "./nav";

export const metadata: Metadata = { title: "field-copilot", description: "One AI copilot platform, deployed for two customers" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Nav />
        <main>{children}</main>
      </body>
    </html>
  );
}

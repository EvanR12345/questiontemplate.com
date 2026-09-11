import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sheep Cube Solver | Wacky Woollies Supercube",
  description:
    "Solve your Wacky Woollies sheep Rubik’s picture cube, including rotated middles. Guided photo scanning, interactive 3D moves and shorter center corrections.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <script
          async
          src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-4610762209559364"
          crossOrigin="anonymous"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}

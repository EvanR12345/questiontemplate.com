import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Picture Supercube Solver | Question Template",
  description:
    "Solve picture pieces and rotated center tiles with guided photo capture and an interactive 3D supercube. Includes a center-only mode for sheep picture cubes.",
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
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ColorWind",
  description: "Pintar por numeros para adultos",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}

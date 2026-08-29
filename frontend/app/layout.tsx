import "./globals.css";

export const metadata = {
  title: "Bank of Mum",
  description: "Family lending, accounting and forecasting workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <a className="scenario-shortcut" href="/scenarios">Scenarios</a>
      </body>
    </html>
  );
}

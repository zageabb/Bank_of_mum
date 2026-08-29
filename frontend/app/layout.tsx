import "./globals.css";

export const metadata = {
  title: "Bank of Mum",
  description: "Family lending, accounting, forecasting and AI workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
        <nav className="phase6-shortcuts" aria-label="Bank of Mum shortcuts">
          <a href="/scenarios">Scenarios</a>
          <a href="/ai">AI</a>
          <a href="/settings">Settings</a>
        </nav>
      </body>
    </html>
  );
}

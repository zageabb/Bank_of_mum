import "./globals.css";
import "./phase7.css";
import Phase6Navigation from "./phase6-navigation";

export const metadata = {
  title: "Bank of Mum",
  description: "Family lending, accounting, forecasting, reporting and AI workspace",
};

// Next.js basePath is configured at build time; use it for plain anchor links too.
const basePath = (process.env.NEXT_PUBLIC_BASE_PATH || "").replace(/\/$/, "");
const shortcutHref = (path: string) => `${basePath}/${path}/`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Phase6Navigation />
        {children}
        <nav className="phase6-shortcuts phase7-shortcuts" aria-label="Bank of Mum shortcuts">
          <a href={shortcutHref("manage")}>People &amp; Accounts</a>
          <a href={shortcutHref("reports")}>Reports</a>
          <a href={shortcutHref("scenarios")}>Scenarios</a>
          <a href={shortcutHref("ai")}>AI</a>
          <a href={shortcutHref("maintenance")}>Maintenance</a>
          <a href={shortcutHref("settings")}>Settings</a>
        </nav>
      </body>
    </html>
  );
}

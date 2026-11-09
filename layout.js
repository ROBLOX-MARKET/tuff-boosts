import { Manrope, Outfit } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/Providers";
import Header from "@/components/Header";
import Footer from "@/components/Footer";
import ChatWidget from "@/components/ChatWidget";

const display = Outfit({ subsets: ["latin"], weight: ["600", "700", "800"], variable: "--font-display" });
const body = Manrope({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-body" });

export const metadata = {
  title: "Tuff Boosts",
  description: "Discord Server Boosts and Nitro. Pay with USDT, Bitcoin or Litecoin."
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: "#07090F"
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={display.variable + " " + body.variable}>
      <body>
        <Providers>
          <Header />
          {children}
          <Footer />
          <ChatWidget />
        </Providers>
      </body>
    </html>
  );
}

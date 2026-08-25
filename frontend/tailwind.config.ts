import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(214.3 31.8% 91.4%)",
        muted: { DEFAULT: "hsl(210 40% 96.1%)", foreground: "hsl(215.4 16.3% 46.7%)" },
        primary: { DEFAULT: "hsl(222.2 47.4% 11.2%)", foreground: "hsl(210 40% 98%)" },
        destructive: { DEFAULT: "hsl(0 84.2% 60.2%)", foreground: "hsl(210 40% 98%)" },
      },
      borderRadius: { lg: "0.5rem" },
    },
  },
  plugins: [],
};
export default config;

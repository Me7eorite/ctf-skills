import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#F7F9FC",
        card: "#FFFFFF",
        line: "#E2E8F0",
        ink: "#0F172A",
        muted: "#64748B",
        accent: "#0EA5E9",
      },
      boxShadow: {
        glow: "0 4px 20px rgba(14, 165, 233, 0.18)",
      },
    },
  },
  plugins: [],
};

export default config;

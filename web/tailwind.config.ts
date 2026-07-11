import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        pitch: "#0b3d2e",
        ink: "#0f172a",
      },
    },
  },
  plugins: [],
};
export default config;

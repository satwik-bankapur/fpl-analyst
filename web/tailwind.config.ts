import type { Config } from "tailwindcss";

// Theme is transcribed from docs/Design/.../FPL Scout Report v2.dc.html §05
// ("Palette & type scale"), which ships a tailwind.config block verbatim.
// Design intent: one sans family, neutral dark surfaces for chrome, hue only
// where it is functional (blue = team state, red/green = out/in).
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 850 is the design's sheet/modal chrome tone, between screen and card.
        surface: { 950: "#111110", 900: "#191918", 850: "#1D1D1C", 800: "#212120", 700: "#2A2A29", 600: "#333332" },
        line: { DEFAULT: "#2E2E2D", soft: "#232322", strong: "#3A3A38", dim: "#262625" },
        ink: {
          0: "#F4F4F2",
          100: "#EDEDEA",
          300: "#C9C9C5",
          500: "#A6A6A2",
          600: "#8A8A85",
          700: "#73736E",
          800: "#5A5A57",
          900: "#4A4A47",
          inverse: "#161615",
        },
        state: { blue: "#8AB4FF", bluebg: "#1E2A40" },
        out: { red: "#E0705A", redbg: "#3B241F" },
        in: { green: "#6FC287", greenbg: "#2E3B2F" },
      },
      fontFamily: {
        sans: ['"Instrument Sans"', "system-ui", "sans-serif"],
      },
      fontSize: {
        eyebrow: ["12px", { lineHeight: "1", letterSpacing: ".12em" }],
        label: ["13px", { lineHeight: "1.2" }],
        body: ["14px", { lineHeight: "1.5" }],
        prose: ["16px", { lineHeight: "1.7" }],
        "prose-lg": ["17px", { lineHeight: "1.7" }],
        title: ["18px", { lineHeight: "1.2", letterSpacing: "-.01em" }],
        "title-lg": ["24px", { lineHeight: "1.2", letterSpacing: "-.015em" }],
        hero: ["29px", { lineHeight: "1.15", letterSpacing: "-.02em" }],
        display: ["48px", { lineHeight: "1.08", letterSpacing: "-.025em" }],
      },
      borderRadius: { DEFAULT: "9px", btn: "10px", card: "12px", frame: "14px" },
      maxWidth: { measure: "64ch" },
      keyframes: {
        sweep: { "0%": { transform: "translateX(-100%)" }, "100%": { transform: "translateX(320%)" } },
        blink: { "0%,60%": { opacity: "1" }, "61%,100%": { opacity: "0.25" } },
        drift: { "0%": { opacity: ".55" }, "50%": { opacity: "1" }, "100%": { opacity: ".55" } },
      },
      animation: {
        sweep: "sweep 2.4s ease-in-out infinite",
        blink: "blink 1.4s steps(1,end) infinite",
        drift: "drift 6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
export default config;

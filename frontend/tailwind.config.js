/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      colors: {
        navy: {
          950: "#07111f",
          900: "#0b1b33",
          800: "#122844",
          700: "#1a365d",
        },
        canvas: "#f4f6f8",
        ink: "#152033",
        muted: "#5c6b80",
        line: "#e3e8ef",
        accent: {
          DEFAULT: "#1f4e8c",
          soft: "#e8f0fb",
        },
        good: "#0e7a4b",
        warn: "#9a6700",
        bad: "#b42318",
      },
      boxShadow: {
        card: "0 1px 2px rgba(15, 35, 64, 0.06)",
      },
    },
  },
  plugins: [],
};

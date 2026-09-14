/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        canvas: "var(--canvas)",
        surface: "var(--surface)",
        sidebar: "var(--sidebar-material)",
        separator: "var(--separator)",
        border: "var(--separator)",
        input: "var(--separator)",
        ring: "var(--accent-blue)",
        background: "var(--canvas)",
        foreground: "var(--label-primary)",
        label: {
          primary: "var(--label-primary)",
          secondary: "var(--label-secondary)",
          tertiary: "var(--label-tertiary)",
        },
        "accent-blue": "rgb(var(--accent-blue-rgb) / <alpha-value>)",
        success: "rgb(var(--success-rgb) / <alpha-value>)",
        warning: "rgb(var(--warning-rgb) / <alpha-value>)",
        danger: "rgb(var(--danger-rgb) / <alpha-value>)",
        primary: {
          DEFAULT: "var(--accent-blue)",
          foreground: "#ffffff",
        },
        secondary: {
          DEFAULT: "var(--surface-muted)",
          foreground: "var(--label-primary)",
        },
        destructive: {
          DEFAULT: "var(--danger)",
          foreground: "#ffffff",
        },
        muted: {
          DEFAULT: "var(--surface-muted)",
          foreground: "var(--label-secondary)",
        },
        accent: {
          DEFAULT: "var(--surface-muted)",
          foreground: "var(--label-primary)",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
}

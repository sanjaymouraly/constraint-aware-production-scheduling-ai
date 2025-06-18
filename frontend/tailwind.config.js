/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        steel: {
          50: '#f4f7fa', 100: '#e6ecf2', 200: '#c7d3e0', 300: '#9fb3c8',
          400: '#7190ad', 500: '#527392', 600: '#3f5c79', 700: '#334b63',
          800: '#2b3f53', 900: '#253646', 950: '#152030',
        },
        accent: {
          50: '#effdf7', 100: '#d6f9ec', 200: '#b0f1dc', 300: '#7ae4c8',
          400: '#41cfaf', 500: '#18b596', 600: '#0a917b', 700: '#0b7466',
          800: '#0d5c52', 900: '#0f4c45', 950: '#032c29',
        },
        warn: { 400: '#fbbf24', 500: '#f59e0b', 600: '#d97706' },
        danger: { 400: '#f87171', 500: '#ef4444', 600: '#dc2626' },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(16 24 40 / 0.06), 0 1px 3px 0 rgb(16 24 40 / 0.10)',
        lift: '0 10px 30px -12px rgb(16 24 40 / 0.25)',
      },
    },
  },
  plugins: [],
}

/** @type {import('tailwindcss').Config} */
export default {
  // 'class' + no .dark anywhere = the console is ALWAYS the light theme
  // (owner 2026-07-22: no black backgrounds on any device). All existing
  // dark: variants become inert without deleting them.
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
}

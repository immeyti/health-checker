/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./src/web/templates/**/*.html"],
  theme: {
    extend: {
      colors: {
        slate: {
          950: '#020617',
        }
      }
    }
  },
  plugins: [],
}

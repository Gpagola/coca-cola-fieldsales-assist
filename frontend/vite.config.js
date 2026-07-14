import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => ({
  base: mode === 'production' ? '/sa/' : '/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:5002'
    }
  }
}))

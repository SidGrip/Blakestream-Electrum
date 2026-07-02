import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ConfirmProvider } from './components/ConfirmModal'
import './styles.css'

const container = document.getElementById('root')
if (!container) {
  throw new Error('Root element #root not found')
}

createRoot(container).render(
  <React.StrictMode>
    <ConfirmProvider>
      <App />
    </ConfirmProvider>
  </React.StrictMode>,
)

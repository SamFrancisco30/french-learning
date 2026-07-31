import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { IdentityProvider } from './identity/IdentityContext'
import { VocabProvider } from './vocab/VocabContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <IdentityProvider>
      <VocabProvider>
        <App />
      </VocabProvider>
    </IdentityProvider>
  </StrictMode>,
)

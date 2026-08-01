import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthContext'
import { IdentityProvider } from './identity/IdentityContext'
import { VocabProvider } from './vocab/VocabContext'

// AuthProvider sits inside IdentityProvider and outside VocabProvider, and both halves of that
// matter. It reads the device key from identity to claim anonymous work on sign-in, and it installs
// the header source that every API call — vocab's included — reads for its bearer token.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <IdentityProvider>
      <AuthProvider>
        <VocabProvider>
          <App />
        </VocabProvider>
      </AuthProvider>
    </IdentityProvider>
  </StrictMode>,
)

// Documentation preview: render the real application with intercepted services.
// This entry is bundled into a temporary directory by capture_screenshots.py.
import { createRoot } from 'react-dom/client'
import Home from '../../src/app/page'
import ThemeProvider from '../../src/components/ThemeProvider'
import { AuthProvider } from '../../src/context/AuthContext'
import { THEME_BOOTSTRAP_SCRIPT } from '../../src/lib/theme'

new Function(THEME_BOOTSTRAP_SCRIPT)()
createRoot(document.getElementById('root')!).render(
  <ThemeProvider>
    <AuthProvider>
      <Home />
    </AuthProvider>
  </ThemeProvider>
)

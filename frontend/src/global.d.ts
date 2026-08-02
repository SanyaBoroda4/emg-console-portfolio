// Minimal typing for the Google Identity Services script (loaded via
// <script> in index.html — no npm package, per the Stage 2 plan).

interface GoogleCredentialResponse {
  credential: string
}

interface Window {
  google?: {
    accounts: {
      id: {
        initialize: (config: {
          client_id: string
          // Popup mode (desktop): GIS invokes the callback with the credential.
          callback?: (response: GoogleCredentialResponse) => void
          // Redirect mode (iOS): GIS form-POSTs the credential to login_uri.
          ux_mode?: 'popup' | 'redirect'
          login_uri?: string
        }) => void
        renderButton: (parent: HTMLElement, options: Record<string, unknown>) => void
      }
    }
  }
}

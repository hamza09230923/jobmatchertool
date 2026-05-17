import { StrictMode, Component } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import "./backendStatus";
import "./index.css"
import App from './App.jsx'

class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError() {
    return { hasError: true };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '100vh', background: '#0f1115', color: '#f4f6fb', gap: '16px', fontFamily: 'system-ui, sans-serif' }}>
          <p style={{ fontSize: '1.1rem', color: '#b8c0d4', margin: 0 }}>Something went wrong. Please refresh the page.</p>
          <button onClick={() => window.location.reload()} style={{ padding: '10px 24px', background: 'rgba(94,228,255,0.08)', border: '1px solid rgba(94,228,255,0.25)', borderRadius: '999px', color: '#5ee4ff', cursor: 'pointer', fontSize: '0.9rem' }}>
            Refresh
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </StrictMode>,
)

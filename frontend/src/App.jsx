import { useEffect, useState } from 'react'
import MainLayout from './components/MainLayout'

function App() {
  const [backendStatus, setBackendStatus] = useState('Checking...')

  useEffect(() => {
    fetch('http://127.0.0.1:8000/health')
      .then((response) => response.json())
      .then((data) => {
        setBackendStatus(data.message)
      })
      .catch(() => {
        setBackendStatus('Backend connection failed')
      })
  }, []) 

  return (
    <>
      <MainLayout />

      <div
        style={{
          position: 'fixed',
          bottom: '15px',
          right: '15px',
          background: '#ffffff',
          border: '1px solid #dee2e6',
          borderRadius: '6px',
          padding: '10px 15px',
          fontSize: '13px',
        }}
      >
        Backend: {backendStatus}
      </div>
    </>
  )
}

export default App
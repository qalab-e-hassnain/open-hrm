import { useEffect } from 'react';
import { useAuthStore } from './store/authStore';
import AppRoutes from './routes/AppRoutes';
import './App.css';
import { BRAND_NAME } from './config/constants';

function App() {
  const { initializeAuth, isLoading } = useAuthStore();

  useEffect(() => {
    initializeAuth();
  }, [initializeAuth]);

  if (isLoading) {
    return (
      <div className="app-loading">
        <div className="spinner"></div>
        <p>Loading {BRAND_NAME}…</p>
      </div>
    );
  }

  return <AppRoutes />;
}

export default App;

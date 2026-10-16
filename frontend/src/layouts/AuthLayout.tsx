import { type ReactNode } from 'react';
import './AuthLayout.css';
import { BRAND_NAME } from '../config/constants';

interface AuthLayoutProps {
  children: ReactNode;
}

const AuthLayout = ({ children }: AuthLayoutProps) => {
  return (
    <div className="auth-layout">
      <div className="auth-container">
        <div className="auth-header">
          <h1>{BRAND_NAME}</h1>
          <p>Human Resource Management System</p>
        </div>
        <div className="auth-content">
          {children}
        </div>
      </div>
    </div>
  );
};

export default AuthLayout;


import { type ReactNode } from 'react';
import './Card.css';

interface CardProps {
  children: ReactNode;
  title?: string;
  className?: string;
  headerAction?: ReactNode;
  icon?: ReactNode;
}

const Card = ({ children, title, className = '', headerAction, icon }: CardProps) => {
  return (
    <div className={`card-component ${className}`}>
      {title && (
        <div className="card-header">
          {icon && <span className="card-title-icon">{icon}</span>}
          <h3 className="card-title">{title}</h3>
          {headerAction && <div className="card-header-action">{headerAction}</div>}
        </div>
      )}
      <div className="card-body">{children}</div>
    </div>
  );
};

export default Card;


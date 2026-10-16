import { type InputHTMLAttributes, forwardRef, useId } from 'react';
import './Input.css';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  icon?: React.ReactNode;
}

const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, icon, className = '', id, ...props }, ref) => {
    // The label has to stay a sibling AFTER the input, because the floating
    // label is driven by :not(:placeholder-shown) on the input. A sibling
    // label is only tied to its field by htmlFor, and that was missing — so
    // every one of these fields went unnamed to a screen reader, and
    // clicking the label did nothing. useId keeps that pairing unique even
    // where the same form is rendered twice on a page.
    const generated = useId();
    const fieldId = id ?? generated;
    const errorId = `${fieldId}-error`;
    return (
      <div className="input-wrapper">
        <div className="input-container">
          <input
            ref={ref}
            id={fieldId}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            /* placeholder=" " is essential for the CSS :not(:placeholder-shown) logic */
            placeholder=" " 
            className={`input-field ${error ? 'input-error' : ''} ${icon ? 'input-with-icon' : ''} ${className}`}
            {...props}
          />
          {label && <label className="input-label" htmlFor={fieldId}>{label}</label>}
          
          {icon && <div className="input-icon">{icon}</div>}
        </div>
        {error && <span className="input-error-message" id={errorId}>{error}</span>}
      </div>
    );
  }
);

Input.displayName = 'Input';

export default Input;
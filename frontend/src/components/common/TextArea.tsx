import { type TextareaHTMLAttributes, forwardRef, useId } from 'react';
import './TextArea.css';

interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
}

const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(
  ({ label, error, className = '', id, ...props }, ref) => {
    // Same reasoning as Input and Select: a label that is a sibling rather
    // than a wrapper names nothing unless htmlFor says which field it belongs
    // to.
    const generated = useId();
    const fieldId = id ?? generated;
    const errorId = `${fieldId}-error`;
    return (
      <div className="textarea-wrapper">
        {label && <label className="textarea-label" htmlFor={fieldId}>{label}</label>}
        <textarea
          ref={ref}
          id={fieldId}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className={`textarea-field ${error ? 'textarea-error' : ''} ${className}`}
          {...props}
        />
        {error && <span className="textarea-error-message" id={errorId}>{error}</span>}
      </div>
    );
  }
);

TextArea.displayName = 'TextArea';

export default TextArea;


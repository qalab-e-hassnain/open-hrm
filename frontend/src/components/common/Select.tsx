import { type SelectHTMLAttributes, forwardRef, useId } from 'react';
import { FiChevronDown } from 'react-icons/fi';
import './Select.css';

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  options: Array<{ value: string; label: string }>;
}

const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ label, error, options, className = '', id, ...props }, ref) => {
    // Same as Input: the label follows the field for the floating-label CSS,
    // so htmlFor is the only thing connecting the two.
    const generated = useId();
    const fieldId = id ?? generated;
    const errorId = `${fieldId}-error`;
    return (
      <div className="select-wrapper">
        <div className="select-container">
          <select
            ref={ref}
            id={fieldId}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className={`select-field ${error ? 'select-error' : ''} ${className}`}
            {...props}
          >
            {/* Hidden empty option for CSS detection */}
            <option value="" disabled hidden></option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {label && <label className="select-label" htmlFor={fieldId}>{label}</label>}
          <div className="select-icon">
            <FiChevronDown size={20} />
          </div>
        </div>
        {error && <span className="select-error-message" id={errorId}>{error}</span>}
      </div>
    );
  }
);

Select.displayName = 'Select';

export default Select;
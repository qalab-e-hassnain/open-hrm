// Shared field-validation rules for Pakistan-based employee records, used by
// the self-service Profile editor, admin user creation, and admin user
// editing so the three forms never drift apart on what "valid" means.

export const normaliseDigits = (value: string) => value.replace(/\D/g, '');

/** "0300 1234567" — accepts a leading +92/92 and rewrites it to the local 0-prefix. */
export const formatPakPhone = (value: string) => {
  let digits = normaliseDigits(value);
  if (digits.startsWith('92')) digits = `0${digits.slice(2)}`;
  digits = digits.slice(0, 11);
  if (digits.length <= 4) return digits;
  if (digits.length <= 7) return `${digits.slice(0, 4)} ${digits.slice(4)}`;
  return `${digits.slice(0, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
};

/** "35201-1234567-8" */
export const formatCnic = (value: string) => {
  const digits = normaliseDigits(value).slice(0, 13);
  if (digits.length <= 5) return digits;
  if (digits.length <= 12) return `${digits.slice(0, 5)}-${digits.slice(5)}`;
  return `${digits.slice(0, 5)}-${digits.slice(5, 12)}-${digits.slice(12)}`;
};

export const NAME_PATTERN = /^[A-Za-z][A-Za-z\s.'-]*$/;
// Departments and job titles: parentheses and periods are allowed because
// real designations carry them — "Chief Technology Officer (CTO)",
// "Sr. Manager" — and rejecting them blocked users from saving a profile
// whose designation an admin had already assigned.
export const ORG_NAME_PATTERN = /^[A-Za-z][A-Za-z\s&'().-]*$/;
export const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// Keystroke-level filters: strip disallowed characters as the user types,
// instead of letting garbage land in the field and only complaining about it
// after the fact. Kept intentionally permissive (e.g. no leading-letter
// check) since that's still enforced by validatePersonName/validateOrgName
// on blur/submit — these just stop the obviously-wrong characters (digits,
// symbols) from ever being entered in a text field they can't belong in.
export const sanitizeName = (value: string) => value.replace(/[^A-Za-z\s.'-]/g, '');
export const sanitizeOrgName = (value: string) => value.replace(/[^A-Za-z\s&'().-]/g, '');
export const sanitizeAccountNumber = (value: string) => normaliseDigits(value).slice(0, 24);
export const sanitizeBranchCode = (value: string) => normaliseDigits(value).slice(0, 6);

export const validatePersonName = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  if (!NAME_PATTERN.test(str)) return 'Use letters, spaces, apostrophes, periods, or hyphens only.';
  return '';
};

export const validateOrgName = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  if (!ORG_NAME_PATTERN.test(str)) return 'Use letters, spaces, or & . ( ) - \' only.';
  return '';
};

export const validateEmail = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  if (!EMAIL_PATTERN.test(str)) return 'Enter a valid email address.';
  return '';
};

export const validatePakPhone = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  if (normaliseDigits(str).length !== 11) return 'Enter an 11-digit Pakistani mobile number.';
  return '';
};

export const validateCnic = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  if (normaliseDigits(str).length !== 13) return 'Enter a valid 13-digit CNIC.';
  return '';
};

export const validateDateOfBirth = (value: string, { required = false } = {}): string => {
  if (!value) return required ? 'This field is required.' : '';
  // The backend returns date_of_birth as a full timestamp ("1990-01-27T00:00:00"),
  // not a bare date — appending T00:00:00 onto that unmodified produces
  // "...T00:00:00T00:00:00", an unparseable string, so this field wrongly
  // failed validation (blocking the whole save, on every tab) any time it
  // was left untouched in an edit session. Slicing to the first 10 chars is
  // a no-op on an already-bare "YYYY-MM-DD" value and safely truncates a
  // full timestamp down to just its date.
  const dob = new Date(`${value.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(dob.getTime())) return 'Enter a valid date.';
  if (dob > new Date()) return 'Date of birth cannot be in the future.';
  const eighteenYearsAgo = new Date();
  eighteenYearsAgo.setFullYear(eighteenYearsAgo.getFullYear() - 18);
  if (dob > eighteenYearsAgo) return 'Employee must be at least 18 years old.';
  return '';
};

export const validateAddress = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  if (str.length < 10) return 'Enter a complete address (at least 10 characters).';
  return '';
};

export const validateAccountNumber = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  const digits = normaliseDigits(str);
  if (digits.length < 5 || digits.length > 24) return 'Enter a valid account number.';
  return '';
};

export const validateBranchCode = (value: string, { required = false } = {}): string => {
  const str = value.trim();
  if (!str) return required ? 'This field is required.' : '';
  const digits = normaliseDigits(str);
  if (digits.length < 3 || digits.length > 6) return 'Enter a valid branch code (3-6 digits).';
  return '';
};

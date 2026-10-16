/**
 * Hours and rate arithmetic for a single employee.
 *
 * Mirrors `app/utils/overtime.py` on the backend: a month is that person's
 * own standard day over 20 working days, not a flat 160 hours. Somebody
 * contracted for 4 hours a day works an 80-hour month, and dividing their
 * salary by 160 would show half the rate they are actually paid — the same
 * error as paying a half-day employee a full-time day rate, in the opposite
 * direction.
 *
 * `standardHours` is hours per DAY. It arrives as 0 when the server has no
 * value, which every helper here reads as full time.
 */

/** 160 is 8 hours over 20 days; naming the days is what makes part time work. */
export const WORKING_DAYS_PER_MONTH = 20;
export const FULL_TIME_HOURS_PER_DAY = 8;
export const FULL_TIME_MONTHLY_HOURS = FULL_TIME_HOURS_PER_DAY * WORKING_DAYS_PER_MONTH;

/** The employee's contracted day, defaulting to full time. */
export const hoursPerDay = (standardHours?: number | null): number =>
  standardHours && standardHours > 0 ? standardHours : FULL_TIME_HOURS_PER_DAY;

/** The employee's month: 160 hours full time, 80 on a 4-hour day. */
export const monthlyHours = (standardHours?: number | null): number =>
  hoursPerDay(standardHours) * WORKING_DAYS_PER_MONTH;

/** Basic salary spread over that employee's own month. */
export const hourlyRate = (baseSalary?: number | null, standardHours?: number | null): number => {
  if (!baseSalary || baseSalary <= 0) return 0;
  return baseSalary / monthlyHours(standardHours);
};

/** "basic ÷ 80" — the divisor spelled out for a label or hint. */
export const rateBasis = (standardHours?: number | null): string =>
  `basic ÷ ${monthlyHours(standardHours)}`;

/** True for anyone contracted below the full-time day. */
export const isPartTime = (standardHours?: number | null): boolean =>
  Boolean(standardHours && standardHours > 0 && standardHours < FULL_TIME_HOURS_PER_DAY);

/** "4 hours a day (part time)" / "8 hours a day". */
export const describeDay = (standardHours?: number | null): string => {
  const hrs = hoursPerDay(standardHours);
  return `${hrs % 1 === 0 ? hrs : hrs.toFixed(1)} hours a day${isPartTime(standardHours) ? ' (part time)' : ''}`;
};

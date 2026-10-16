import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export interface MonthRange {
  startYear: number;
  startMonth: number; // 1-12
  endYear: number;
  endMonth: number; // 1-12
}

export interface TaxCertificateRow {
  month: number;
  year: number;
  month_label: string;
  total_income: number;
  exempt_income: number;
  taxable_income: number;
  tax_paid: number;
  cumulative_tax: number;
}

export interface TaxCertificate {
  company: { name: string; reg_no: string; address: string };
  employee: { name: string; cnic?: string; employee_id?: string; designation?: string };
  period_label: string;
  currency: string;
  rows: TaxCertificateRow[];
  totals: {
    total_income: number;
    exempt_income: number;
    taxable_income: number;
    tax_paid: number;
  };
}

const rangeParams = (r?: MonthRange) =>
  r
    ? { start_year: r.startYear, start_month: r.startMonth, end_year: r.endYear, end_month: r.endMonth }
    : undefined;

export const taxService = {
  // Logged-in user's own certificate. Omit range for the current tax year to date.
  getMyTaxCertificate: async (range?: MonthRange): Promise<TaxCertificate> => {
    const response = await apiClient.get<TaxCertificate>(API_ENDPOINTS.TAX_CERTIFICATE_ME, {
      params: rangeParams(range),
    });
    return response.data as TaxCertificate;
  },

  // Admin: any employee's certificate over a month range.
  getEmployeeTaxCertificate: async (employeeId: string, range?: MonthRange): Promise<TaxCertificate> => {
    const response = await apiClient.get<TaxCertificate>(API_ENDPOINTS.TAX_CERTIFICATE_BY_ID(employeeId), {
      params: rangeParams(range),
    });
    return response.data as TaxCertificate;
  },
};

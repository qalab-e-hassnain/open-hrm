import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useAuthStore } from '../store/authStore';
import { ROUTES } from '../config/constants';
import { isPrivileged } from '../utils/permissions';

// Layouts
import MainLayout from '../layouts/MainLayout';

// Auth Pages
import Login from '../pages/auth/Login';
const Register = lazy(() => import('../pages/auth/Register'));
const NotFound = lazy(() => import('../pages/common/NotFound'));
const Terms = lazy(() => import('../pages/auth/terms')); // Public Terms & Policy page
const VerifyPayslip = lazy(() => import('../pages/public/VerifyPayslip'));
const VerifyEmployment = lazy(() => import('../pages/public/VerifyEmployment'));
const VerifyDocument = lazy(() => import('../pages/public/VerifyDocument'));
// Employee Pages
import EmployeeDashboard from '../pages/employee/Dashboard';
const Profile = lazy(() => import('../pages/employee/Profile'));
const Payslips = lazy(() => import('../pages/employee/Payslips'));
const AttendanceHistory = lazy(() => import('../pages/employee/AttendanceHistory'));
const TimeLogs = lazy(() => import('../pages/employee/TimeLogs'));
const MyDocuments = lazy(() => import('../pages/employee/MyDocuments'));
const Onboarding = lazy(() => import('../pages/employee/Onboarding'));
const Offboarding = lazy(() => import('../pages/employee/Offboarding'));
const TaxCertificate = lazy(() => import('../pages/employee/TaxCertificate'));
const EmploymentLetter = lazy(() => import('../pages/employee/EmploymentLetter'));
// Common Pages
const Leaves = lazy(() => import('../pages/common/Leaves'));
const Reimbursements = lazy(() => import('../pages/common/Reimbursements'));

// Manager Pages
import ManagerDashboard from '../pages/manager/Dashboard';

// Admin Pages
import AdminDashboard from '../pages/admin/Dashboard';
const Employees = lazy(() => import('../pages/admin/Employees'));
const HrDocuments = lazy(() => import('../pages/admin/HrDocuments'));
const Probation = lazy(() => import('../pages/admin/Probation'));
const ScheduledJobs = lazy(() => import('../pages/admin/ScheduledJobs'));
const BiometricDevice = lazy(() => import('../pages/admin/BiometricDevice'));
const Analytics = lazy(() => import('../pages/admin/Analytics'));
const OrgChart = lazy(() => import('../pages/admin/OrgChart'));
const Advances = lazy(() => import('../pages/employee/Advances'));
const Shifts = lazy(() => import('../pages/admin/Shifts'));
const Performance = lazy(() => import('../pages/employee/Performance'));
const Awards = lazy(() => import('../pages/employee/Awards'));
const Handbook = lazy(() => import('../pages/common/Handbook'));
const PayrollReimbursement = lazy(() => import('../pages/admin/PayrollReimbursement'));

// Lab Inventory
const InventoryScan = lazy(() => import('../pages/inventory/Scan'));
const InventoryCatalogue = lazy(() => import('../pages/inventory/Catalogue'));
const InventoryItemDetail = lazy(() => import('../pages/inventory/ItemDetail'));
const InventoryReceiving = lazy(() => import('../pages/inventory/Receiving'));
const MyEquipment = lazy(() => import('../pages/inventory/MyHoldings'));
const InventoryRequests = lazy(() => import('../pages/inventory/Requests'));
const InventoryAudit = lazy(() => import('../pages/inventory/Audit'));
const InventoryAuditSession = lazy(() => import('../pages/inventory/AuditSession'));
const InventoryReports = lazy(() => import('../pages/inventory/Reports'));

// Protected Route Component
const ProtectedRoute = ({
  children,
  allowedRoles
}: {
  children: React.ReactNode;
  allowedRoles?: string[];
}) => {
  const { isAuthenticated, user } = useAuthStore();

  if (!isAuthenticated) {
    return <Navigate to={ROUTES.LOGIN} replace />;
  }

  // An HR manager passes anywhere an admin does — same reach, same screens.
  if (allowedRoles && user && !allowedRoles.includes(user.role)
      && !(allowedRoles.includes('admin') && isPrivileged(user))) {
    return <Navigate to={ROUTES.DASHBOARD} replace />;
  }

  return <>{children}</>;
};

// Public Route Component (redirect if already authenticated)
const PublicRoute = ({ children }: { children: React.ReactNode }) => {
  const { isAuthenticated } = useAuthStore();

  if (isAuthenticated) {
    return <Navigate to={ROUTES.DASHBOARD} replace />;
  }

  return <>{children}</>;
};

const AppRoutes = () => {
  return (
    <BrowserRouter>
      <Suspense fallback={<div className="route-loading" aria-busy="true" />}>
      <Routes>

        {/* Public Routes */}
        <Route
          path={ROUTES.LOGIN}
          element={
            <PublicRoute>
              <Login />
            </PublicRoute>
          }
        />

        <Route
          path={ROUTES.REGISTER}
          element={
            <PublicRoute>
              <Register />
            </PublicRoute>
          }
        />

        {/* Public Terms page - accessible to all users (no redirect on auth) */}
        <Route path={ROUTES.TERMS} element={<Terms />} />
        {/* Public, and deliberately not wrapped in PublicRoute: the audience is
            a bank or employer with no account, and an admin checking a code
            should not be bounced to the dashboard either. The :code form lets
            the address printed on a payslip resolve in a single click. */}
        <Route path={ROUTES.VERIFY_PAYSLIP} element={<VerifyPayslip />} />
        <Route path={`${ROUTES.VERIFY_PAYSLIP}/:code`} element={<VerifyPayslip />} />
        <Route path={ROUTES.VERIFY_EMPLOYMENT} element={<VerifyEmployment />} />
        <Route path={`${ROUTES.VERIFY_EMPLOYMENT}/:code`} element={<VerifyEmployment />} />
        <Route path={ROUTES.VERIFY_DOCUMENT} element={<VerifyDocument />} />
        <Route path={`${ROUTES.VERIFY_DOCUMENT}/:code`} element={<VerifyDocument />} />

        {/* Protected Routes - All Roles */}
        <Route
          path={ROUTES.DASHBOARD}
          element={
            <ProtectedRoute>
              <MainLayout>
                <DashboardRouter />
              </MainLayout>
            </ProtectedRoute>
          }
        />

        <Route
          path={ROUTES.PROFILE}
          element={
            <ProtectedRoute>
              <MainLayout>
                <Profile />
              </MainLayout>
            </ProtectedRoute>
          }
        />

        {/* Employee Routes - Accessible by all roles (employee, manager, admin) */}
        <Route
          path={ROUTES.LEAVES}
          element={
            <ProtectedRoute>
              <MainLayout>
                <Leaves />
              </MainLayout>
            </ProtectedRoute>
          }
        />

        <Route
          path={ROUTES.REIMBURSEMENTS}
          element={
            <ProtectedRoute>
              <MainLayout>
                <Reimbursements />
              </MainLayout>
            </ProtectedRoute>
          }
        />

        {/* Backward compatibility routes - redirect to consolidated pages */}
        <Route
          path={ROUTES.APPLY_LEAVE}
          element={<Navigate to={ROUTES.LEAVES} replace />}
        />

        <Route
          path={ROUTES.REIMBURSEMENT}
          element={<Navigate to={ROUTES.REIMBURSEMENTS} replace />}
        />

        <Route
          path={ROUTES.PAYSLIPS}
          element={
            <ProtectedRoute>
              <MainLayout>
                <Payslips />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        {/* The page was written but never routed, so nobody could reach their
            own certificate. Open to any signed-in user: the page shows the
            employee picker to admins only, and the API serves the caller's own
            record otherwise. */}
        <Route
          path={ROUTES.TAX_CERTIFICATE}
          element={
            <ProtectedRoute>
              <MainLayout>
                <TaxCertificate />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.EMPLOYMENT_LETTER}
          element={
            <ProtectedRoute>
              <MainLayout>
                <EmploymentLetter />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.ATTENDANCE_HISTORY}
          element={
            <ProtectedRoute>
              <MainLayout>
                <AttendanceHistory />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.ONBOARDING}
          element={
            <ProtectedRoute>
              <MainLayout>
                <Onboarding />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.OFFBOARDING}
          element={
            <ProtectedRoute>
              <MainLayout>
                <Offboarding />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.DOCUMENTS}
          element={
            <ProtectedRoute>
              <MainLayout>
                <MyDocuments />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.TIME_LOGS}
          element={
            <ProtectedRoute>
              <MainLayout>
                <TimeLogs />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.LEAVE_POLICY}
          element={<Navigate to={`${ROUTES.LEAVES}?policy=open`} replace />}
        />

        {/* Backward compatibility routes - redirect to consolidated pages */}
        <Route
          path={ROUTES.MANAGE_LEAVES}
          element={<Navigate to={ROUTES.LEAVES} replace />}
        />

        <Route
          path={ROUTES.MANAGE_REIMBURSEMENT}
          element={<Navigate to={ROUTES.REIMBURSEMENTS} replace />}
        />

        {/* Admin Routes */}
        {/* A line manager decides their own team's probations, so this is not
            admin-only; the server still gates each row with can_decide. */}
        <Route
          path={ROUTES.PROBATION}
          element={
            <ProtectedRoute allowedRoles={['admin', 'manager']}>
              <MainLayout>
                <Probation />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        {/* Everybody, no role gate: it is the book about working here. */}
        <Route
          path={ROUTES.HANDBOOK}
          element={
            <ProtectedRoute>
              <MainLayout><Handbook /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.AWARDS}
          element={
            <ProtectedRoute>
              <MainLayout><Awards /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.PERFORMANCE}
          element={
            <ProtectedRoute>
              <MainLayout><Performance /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.SHIFTS}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout><Shifts /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.LOANS}
          element={
            <ProtectedRoute>
              <MainLayout><Advances /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.ANALYTICS}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout><Analytics /></MainLayout>
            </ProtectedRoute>
          }
        />
        {/* Readable by any authenticated user — it is the directory, and the
            server returns only what a colleague may see. */}
        <Route
          path={ROUTES.ORG_CHART}
          element={
            <ProtectedRoute>
              <MainLayout><OrgChart /></MainLayout>
            </ProtectedRoute>
          }
        />

        {/* Lab inventory. Open to every authenticated user by design: the
            catalogue is for everybody, the scan screen is how anything gets
            logged at all, and what a given person may DO to a given item is
            the item's own `actions[]`, not the route. Receiving is a custodian
            job the nav only offers to custodians — the backend, not the
            router, is what refuses the create. */}
        <Route
          path={ROUTES.INVENTORY_SCAN}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryScan /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.INVENTORY_ITEMS}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryCatalogue /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={`${ROUTES.INVENTORY_ITEMS}/:id`}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryItemDetail /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.INVENTORY_RECEIVE}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryReceiving /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.MY_EQUIPMENT}
          element={
            <ProtectedRoute>
              <MainLayout><MyEquipment /></MainLayout>
            </ProtectedRoute>
          }
        />
        {/* Requests are everybody's — the server returns only your own unless
            you lead a team or keep the store. The audit and the reports are
            custodian work: the nav offers them to custodians, and the backend
            is what refuses anybody else. */}
        <Route
          path={ROUTES.INVENTORY_REQUESTS}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryRequests /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.INVENTORY_AUDIT}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryAudit /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={`${ROUTES.INVENTORY_AUDIT}/:id`}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryAuditSession /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.INVENTORY_REPORTS}
          element={
            <ProtectedRoute>
              <MainLayout><InventoryReports /></MainLayout>
            </ProtectedRoute>
          }
        />
        {/* Office equipment — the other section of the same register. Issued
            and taken back by admin or HR only (the lab custodian has no reach
            here), so unlike the lab screens these routes are guarded. They are
            the lab screens over `domain="office"`; the key keeps a lab and an
            office instance from sharing filter state across a navigation.
            Single items open on the shared item page. */}
        <Route
          path={ROUTES.OFFICE_EQUIPMENT}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout><InventoryCatalogue key="office" domain="office" /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.OFFICE_EQUIPMENT_NEW}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout><InventoryReceiving key="office" domain="office" /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.OFFICE_EQUIPMENT_REPORTS}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout><InventoryReports key="office" domain="office" /></MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.ADMIN_JOBS}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout>
                <ScheduledJobs />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        {/* Every biometric route is require_admin_or_hr on the server; the
            'admin' guard here lets an HR manager through too, via
            isPrivileged, so the screen and the API admit the same people. */}
        <Route
          path={ROUTES.BIOMETRIC}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout>
                <BiometricDevice />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.HR_DOCUMENTS}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout>
                <HrDocuments />
              </MainLayout>
            </ProtectedRoute>
          }
        />
        <Route
          path={ROUTES.EMPLOYEES}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout>
                <Employees />
              </MainLayout>
            </ProtectedRoute>
          }
        />

        <Route
          path={ROUTES.PAYROLL}
          element={
            <ProtectedRoute allowedRoles={['admin']}>
              <MainLayout>
                <PayrollReimbursement />
              </MainLayout>
            </ProtectedRoute>
          }
        />

        {/* Default Route */}
        <Route path="/" element={<Navigate to={ROUTES.LOGIN} replace />} />

        {/* An unknown address says so, rather than quietly redirecting. */}
        <Route path="*" element={<NotFound />} />
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
};

// Dashboard Router - shows role-appropriate dashboard
const DashboardRouter = () => {
  const { user } = useAuthStore();

  // Show role-specific dashboard
  if (user?.role === 'manager') {
    return <ManagerDashboard />;
  }

  if (user?.role === 'admin') {
    return <AdminDashboard />;
  }

  return <EmployeeDashboard />;
};

export default AppRoutes;

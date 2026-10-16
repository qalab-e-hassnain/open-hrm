import { apiClient } from './api';
import { API_ENDPOINTS } from '../config/constants';

export type CycleStatus = 'draft' | 'open' | 'closed';
export type GoalSource = 'job_profile' | 'manager' | 'employee';
export type GoalStatus = 'active' | 'achieved' | 'partial' | 'missed' | 'dropped';
export type ReviewStatus =
  | 'draft'
  | 'self_submitted'
  | 'manager_submitted'
  | 'shared'
  | 'closed';

export interface Cycle {
  id: number;
  name: string;
  period_start: string;
  period_end: string;
  self_assessment_due?: string | null;
  manager_review_due?: string | null;
  status: CycleStatus;
  instructions?: string | null;
  review_count: number;
  completed_count: number;
}

export interface Goal {
  id: number;
  employee_id: number;
  employee_name?: string | null;
  cycle_id?: number | null;
  title: string;
  description?: string | null;
  /** `job_profile` means it came from a KPI in the signed contract. */
  source: GoalSource;
  weight: number;
  target?: string | null;
  status: GoalStatus;
  rating?: number | null;
  rating_comment?: string | null;
  employee_comment?: string | null;
  created_at: string;
}

export interface Review {
  id: number;
  cycle_id: number;
  cycle_name?: string | null;
  employee_id: number;
  employee_name?: string | null;
  manager_id?: number | null;
  manager_name?: string | null;
  status: ReviewStatus;
  self_assessment?: string | null;
  self_rating?: number | null;
  self_submitted_at?: string | null;
  /**
   * Redacted server-side until the review is shared: this and every field
   * below it come back null to the subject. Branch on `status` rather than
   * assuming they are populated.
   */
  manager_assessment?: string | null;
  overall_rating?: number | null;
  strengths?: string | null;
  development_areas?: string | null;
  manager_submitted_at?: string | null;
  shared_at?: string | null;
  employee_comment?: string | null;
  acknowledged_at?: string | null;
  /** Always null for the subject. */
  calibration_note?: string | null;
  goals: Goal[];
}

export interface OneOnOne {
  id: number;
  manager_id: number;
  manager_name?: string | null;
  employee_id: number;
  employee_name?: string | null;
  meeting_date: string;
  agenda?: string | null;
  shared_notes?: string | null;
  /** Null to anyone but the manager who wrote it — HR included. */
  private_notes?: string | null;
  action_items: { text?: string; owner?: string; done?: boolean }[];
  created_at: string;
}

export const CYCLE_STATUS_LABEL: Record<CycleStatus, string> = {
  draft: 'Draft', open: 'Open', closed: 'Closed',
};

export const REVIEW_STATUS_LABEL: Record<ReviewStatus, string> = {
  draft: 'Not started',
  self_submitted: 'With your manager',
  manager_submitted: 'Written, not yet shared',
  shared: 'Ready to read',
  closed: 'Complete',
};

export const GOAL_STATUS_LABEL: Record<GoalStatus, string> = {
  active: 'Active', achieved: 'Achieved', partial: 'Partly met',
  missed: 'Missed', dropped: 'Dropped',
};

export const GOAL_SOURCE_LABEL: Record<GoalSource, string> = {
  job_profile: 'From your contract', manager: 'Set by your manager', employee: 'Yours',
};

export const performanceService = {
  cycles: async (): Promise<Cycle[]> => {
    const res = await apiClient.get<Cycle[]>(API_ENDPOINTS.PERF_CYCLES);
    return Array.isArray(res.data) ? res.data : [];
  },
  createCycle: async (body: {
    name: string; period_start: string; period_end: string;
    self_assessment_due?: string; manager_review_due?: string; instructions?: string;
  }): Promise<Cycle> => {
    const res = await apiClient.post<Cycle>(API_ENDPOINTS.PERF_CYCLES, body);
    return res.data as Cycle;
  },
  /** Raises one review per active employee and notifies them. */
  openCycle: async (id: number, includeProbationers = false): Promise<Cycle> => {
    const res = await apiClient.post<Cycle>(API_ENDPOINTS.PERF_CYCLE_OPEN(id), {}, {
      params: includeProbationers ? { include_probationers: true } : undefined,
    });
    return res.data as Cycle;
  },
  closeCycle: async (id: number): Promise<Cycle> => {
    const res = await apiClient.post<Cycle>(API_ENDPOINTS.PERF_CYCLE_CLOSE(id), {});
    return res.data as Cycle;
  },

  goals: async (params?: { employee_id?: number; cycle_id?: number }): Promise<Goal[]> => {
    const res = await apiClient.get<Goal[]>(API_ENDPOINTS.PERF_GOALS, { params });
    return Array.isArray(res.data) ? res.data : [];
  },
  addGoal: async (body: {
    employee_id: number; cycle_id?: number; title: string;
    description?: string; weight?: number; target?: string;
  }): Promise<Goal> => {
    const res = await apiClient.post<Goal>(API_ENDPOINTS.PERF_GOALS, body);
    return res.data as Goal;
  },
  /**
   * Turns the KPIs on that employee's job profile into goals. Those are the
   * same KPIs printed in the contract they signed, so nobody is measured
   * against a standard they never agreed to. Skips anything already seeded,
   * and 404s when the designation has no profile or the profile has no KPIs.
   */
  seedGoals: async (employeeId: number, cycleId: number): Promise<Goal[]> => {
    const res = await apiClient.post<Goal[]>(API_ENDPOINTS.PERF_GOALS_SEED, {}, {
      params: { employee_id: employeeId, cycle_id: cycleId },
    });
    return Array.isArray(res.data) ? res.data : [];
  },
  /** An employee's rating, weight and status edits are dropped silently;
   *  their employee_comment lands. */
  updateGoal: async (id: number, body: Partial<{
    title: string; description: string; weight: number; target: string;
    status: GoalStatus; rating: number; rating_comment: string; employee_comment: string;
  }>): Promise<Goal> => {
    const res = await apiClient.put<Goal>(API_ENDPOINTS.PERF_GOAL(id), body);
    return res.data as Goal;
  },
  /** 400 once rated — set status "dropped" instead. */
  deleteGoal: async (id: number): Promise<void> => {
    await apiClient.delete(API_ENDPOINTS.PERF_GOAL(id));
  },

  reviews: async (scope: 'mine' | 'team' | 'all' = 'mine', cycleId?: number): Promise<Review[]> => {
    const res = await apiClient.get<Review[]>(API_ENDPOINTS.PERF_REVIEWS, {
      params: { scope, ...(cycleId ? { cycle_id: cycleId } : {}) },
    });
    return Array.isArray(res.data) ? res.data : [];
  },
  review: async (id: number): Promise<Review> => {
    const res = await apiClient.get<Review>(API_ENDPOINTS.PERF_REVIEW(id));
    return res.data as Review;
  },
  saveSelf: async (id: number, body: {
    self_assessment: string; self_rating?: number; submit: boolean;
  }): Promise<Review> => {
    const res = await apiClient.put<Review>(API_ENDPOINTS.PERF_REVIEW_SELF(id), body);
    return res.data as Review;
  },
  /** 400 while the review is still draft — the employee writes first. */
  saveManager: async (id: number, body: {
    manager_assessment: string; overall_rating?: number; strengths?: string;
    development_areas?: string; calibration_note?: string; submit: boolean;
  }): Promise<Review> => {
    const res = await apiClient.put<Review>(API_ENDPOINTS.PERF_REVIEW_MANAGER(id), body);
    return res.data as Review;
  },
  /** Deliberately separate from writing it. */
  share: async (id: number): Promise<Review> => {
    const res = await apiClient.post<Review>(API_ENDPOINTS.PERF_REVIEW_SHARE(id), {});
    return res.data as Review;
  },
  acknowledge: async (id: number, employee_comment?: string): Promise<Review> => {
    const res = await apiClient.post<Review>(API_ENDPOINTS.PERF_REVIEW_ACK(id), { employee_comment });
    return res.data as Review;
  },

  oneOnOnes: async (employeeId?: number): Promise<OneOnOne[]> => {
    const res = await apiClient.get<OneOnOne[]>(API_ENDPOINTS.PERF_ONE_ON_ONES, {
      params: employeeId ? { employee_id: employeeId } : undefined,
    });
    return Array.isArray(res.data) ? res.data : [];
  },
  addOneOnOne: async (body: {
    employee_id: number; meeting_date: string; agenda?: string;
    shared_notes?: string; private_notes?: string;
    action_items?: { text: string; owner: string; done: boolean }[];
  }): Promise<OneOnOne> => {
    const res = await apiClient.post<OneOnOne>(API_ENDPOINTS.PERF_ONE_ON_ONES, body);
    return res.data as OneOnOne;
  },
};

// ---------------------------------------------------------------------------
// The review as a form the server describes.
//
// Every field arrives labelled with who answers it and whether this viewer may
// answer it right now. The rules about who fills what and when live in
// GET /reviews/{id}/form, so the screen renders what it is given rather than
// re-deriving them and drifting out of step with the write endpoints.
// ---------------------------------------------------------------------------

export type Recommendation = 'none' | 'increment' | 'promotion' | 'development_plan';

export interface RatingScalePoint { label: string; description?: string }

export interface FormCompetency {
  key: string;
  title: string;
  description?: string | null;
  weight: number;
  rated_by?: string;
  /** Both sides rate the same competency separately; the gap is the point. */
  self: { rating?: number | null; comment?: string | null; editable: boolean };
  manager: { rating?: number | null; comment?: string | null; editable: boolean };
}

export interface FormSection {
  key: string;
  title: string;
  prompt?: string | null;
  rated_by: 'employee' | 'manager';
  required?: boolean;
  value?: string | null;
  editable: boolean;
}

export interface FormGoal {
  id: number;
  title: string;
  description?: string | null;
  target?: string | null;
  weight: number;
  source: GoalSource;
  status: GoalStatus;
  employee_comment?: string | null;
  rating?: number | null;
  rating_comment?: string | null;
  /** The employee comments on their goals; they do not rate themselves. */
  employee_can_comment: boolean;
  manager_can_rate: boolean;
}

export interface ReviewForm {
  review_id: number;
  status: ReviewStatus;
  employee_name?: string | null;
  designation?: string | null;
  cycle_name?: string | null;
  template_name?: string | null;
  template_id?: number | null;
  rating_scale: Record<string, RatingScalePoint | string>;
  goal_weight: number;
  competency_weight: number;
  your_part: 'employee' | 'manager' | 'viewer';
  you_can_edit: boolean;
  /** Plain English: "the manager's review", "the employee to acknowledge it". */
  waiting_on: string;
  competencies: FormCompetency[];
  sections: FormSection[];
  goals: FormGoal[];
}

export interface ScoreWorking {
  goal_score?: number | null;
  competency_score?: number | null;
  final_score?: number | null;
  band?: string | null;
  [key: string]: unknown;
}

export interface LeaderboardRow {
  /** Null for an unscored review: listed, but not ranked. Leaving those out
   *  would rank managers rather than employees. */
  rank?: number | null;
  employee_id: number;
  employee_name: string;
  designation?: string | null;
  department?: string | null;
  manager_name?: string | null;
  status: ReviewStatus;
  goal_score?: number | null;
  competency_score?: number | null;
  final_score?: number | null;
  band?: string | null;
  recommendation?: Recommendation | null;
  goals_achieved: number;
  goals_total: number;
}

export const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  none: 'No change',
  increment: 'Increment',
  promotion: 'Promotion',
  development_plan: 'Development plan',
};

export const formService = {
  form: async (reviewId: number): Promise<ReviewForm> => {
    const res = await apiClient.get<ReviewForm>(API_ENDPOINTS.PERF_FORM(reviewId));
    return res.data as ReviewForm;
  },
  /** Withheld from the employee until shared — a score is the one thing
   *  nobody should learn before the conversation. */
  score: async (reviewId: number): Promise<ScoreWorking> => {
    const res = await apiClient.get<ScoreWorking>(API_ENDPOINTS.PERF_SCORE(reviewId));
    return res.data as ScoreWorking;
  },
  leaderboard: async (cycleId?: number): Promise<LeaderboardRow[]> => {
    const res = await apiClient.get<LeaderboardRow[]>(API_ENDPOINTS.PERF_LEADERBOARD, {
      params: cycleId ? { cycle_id: cycleId } : undefined,
    });
    return Array.isArray(res.data) ? res.data : [];
  },
  cycleCharts: async (cycleId: number): Promise<Record<string, unknown>> => {
    const res = await apiClient.get<Record<string, unknown>>(API_ENDPOINTS.PERF_CHARTS_CYCLE(cycleId));
    return (res.data as Record<string, unknown>) ?? {};
  },
  employeeCharts: async (employeeId: number): Promise<Record<string, unknown>> => {
    const res = await apiClient.get<Record<string, unknown>>(API_ENDPOINTS.PERF_CHARTS_EMPLOYEE(employeeId));
    return (res.data as Record<string, unknown>) ?? {};
  },

  saveSelfForm: async (reviewId: number, body: {
    scores?: Record<string, { rating?: number; comment?: string }>;
    sections?: Record<string, string | null>;
    goal_comments?: Record<number, string>;
    submit: boolean;
  }) => {
    const res = await apiClient.put<Review>(API_ENDPOINTS.PERF_REVIEW_SELF(reviewId), body);
    return res.data as Review;
  },
  saveManagerForm: async (reviewId: number, body: {
    scores?: Record<string, { rating?: number; comment?: string }>;
    sections?: Record<string, string | null>;
    goal_ratings?: Record<number, { rating?: number; comment?: string }>;
    strengths?: string;
    development_areas?: string;
    calibration_note?: string;
    recommendation?: Recommendation;
    submit: boolean;
  }) => {
    const res = await apiClient.put<Review>(API_ENDPOINTS.PERF_REVIEW_MANAGER(reviewId), body);
    return res.data as Review;
  },
};

// ---------------------------------------------------------------------------
// Employee of the month — monthly and separate, because reviews are
// half-yearly and an award that waits for them is not an award.
// ---------------------------------------------------------------------------

export type AwardStatus = 'open' | 'decided' | 'cancelled';

export interface Nomination {
  id: number;
  nominee_id: number;
  nominee_name?: string | null;
  nominated_by: number;
  nominator_name?: string | null;
  reason: string;
  created_at: string;
}

export interface Award {
  id: number;
  month: number;
  year: number;
  status: AwardStatus;
  winner_id?: number | null;
  winner_name?: string | null;
  citation?: string | null;
  decided_at?: string | null;
  announced_at?: string | null;
  nominations_close_on?: string | null;
  nomination_count: number;
  /**
   * Empty for everyone but whoever decides, while nominations are open. A
   * visible running count turns nominating into voting for whoever is ahead.
   */
  tally: { label?: string; name?: string; value?: number; count?: number }[];
  nominations: Nomination[];
  you_have_nominated: boolean;
}

/** The month's actual record beside each name. Not a formula — whoever
 *  decides still decides, but they should be able to see that the
 *  most-nominated person was on leave for three weeks. */
export interface AwardSignal {
  employee_id: number;
  employee_name: string;
  designation?: string | null;
  department?: string | null;
  nominations: number;
  attendance_days: number;
  working_days_in_month: number;
  hours_worked: number;
  late_days: number;
  approved_overtime_hours: number;
  leave_days: number;
  goals_achieved_to_date: number;
  last_review_score?: number | null;
  attendance_exempt: boolean;
}

export interface AwardSignals {
  award_id: number;
  month: number;
  year: number;
  period: string;
  working_days: number;
  candidates: AwardSignal[];
}

export const awardService = {
  list: async (): Promise<Award[]> => {
    const res = await apiClient.get<Award[]>(API_ENDPOINTS.AWARDS);
    return Array.isArray(res.data) ? res.data : [];
  },
  current: async (): Promise<Award | null> => {
    const res = await apiClient.get<Award | null>(API_ENDPOINTS.AWARD_CURRENT);
    return (res.data as Award | null) ?? null;
  },
  open: async (body: { month: number; year: number; nominations_close_on?: string }): Promise<Award> => {
    const res = await apiClient.post<Award>(API_ENDPOINTS.AWARDS, body);
    return res.data as Award;
  },
  /** You cannot nominate yourself, and a second nomination for the same
   *  person edits your reason rather than counting twice. */
  nominate: async (awardId: number, nomineeId: number, reason: string): Promise<Award> => {
    const res = await apiClient.post<Award>(API_ENDPOINTS.AWARD_NOMINATE(awardId), {
      nominee_id: nomineeId, reason,
    });
    return res.data as Award;
  },
  withdraw: async (awardId: number, nomineeId: number): Promise<Award> => {
    const res = await apiClient.delete<Award>(API_ENDPOINTS.AWARD_WITHDRAW(awardId, nomineeId));
    return res.data as Award;
  },
  signals: async (awardId: number): Promise<AwardSignals> => {
    const res = await apiClient.get<AwardSignals>(API_ENDPOINTS.AWARD_SIGNALS(awardId));
    return res.data as AwardSignals;
  },
  /** A citation is required to announce a winner; `no_award: true` records a
   *  month nobody won, which is a real outcome. */
  decide: async (awardId: number, body: {
    winner_id?: number; citation?: string; no_award?: boolean; announce?: boolean;
  }): Promise<Award> => {
    const res = await apiClient.post<Award>(API_ENDPOINTS.AWARD_DECIDE(awardId), body);
    return res.data as Award;
  },
};

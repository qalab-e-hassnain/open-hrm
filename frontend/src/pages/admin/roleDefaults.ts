/**
 * Starting content for a job profile, by role.
 *
 * These are drafts to edit, not finished job descriptions — nothing here is
 * written to a profile until HR reviews it and saves. The point is that
 * filling a dozen profiles from an empty box is a task nobody does, whereas
 * correcting a plausible draft is a task people finish. What ends up in a
 * contract is whatever HR leaves behind after editing.
 *
 * REPLACE THESE WITH YOUR OWN. What ships here is deliberately generic — a
 * handful of common roles and obligations that apply almost anywhere. Your
 * job descriptions are your own document: they reflect how your company
 * works, and in most places they form part of an employment contract.
 *
 * Matching is by keyword, not exact title, because designations in use are
 * free text — "Chief Technology Officer (CTO)", "Sr. Backend Engineer" and
 * "Backend Engineer" all have to land somewhere sensible.
 */

export interface RoleDefaults {
  summary: string;
  responsibilities: string[];
  kpis: string[];
  /** Appendix A prints these, and the contract's reporting clause reads from
   *  reportsTo: "The Employee shall directly report to ___". */
  department?: string;
  reportsTo?: string;
}

/** Obligations most salaried roles carry, appended to every set. */
const COMMON_RESPONSIBILITIES = [
  'Keep your manager informed of progress, and raise a blocker the day it appears rather than at the deadline.',
  'Record your own work: time, leave and expenses, accurately and on time.',
  'Handle company and client information in line with the confidentiality terms of your contract.',
  'Look after the equipment issued to you and return it when it is no longer needed.',
];

/** Indicators that apply to anyone who leads a team. */
const MANAGER_KPIS = [
  'Work in the team is delivered to the agreed scope and date.',
  'Every direct report has a current job profile and a review on record.',
  'Decisions and their reasons are written down where the team can find them.',
];

const LEAD_RESPONSIBILITIES = [
  'Plan and assign the team’s work, and keep the plan current as it changes.',
  'Hold regular one-to-ones, and raise a performance concern early and in writing.',
  'Review the team’s output before it leaves the team.',
];

const GENERIC: RoleDefaults = {
  summary: 'Delivers the work of their role to the standard and timetable agreed with their manager.',
  responsibilities: [
    'Carry out the duties of the role as directed.',
    ...COMMON_RESPONSIBILITIES,
  ],
  kpis: [
    'Agreed work is delivered on time and to specification.',
    'Quality of output, as judged at review.',
  ],
  reportsTo: 'Line Manager',
};

/** A few common shapes, as examples. Add your own. */
const LIBRARY: { match: string[]; defaults: RoleDefaults }[] = [
  {
    match: [' engineer ', ' developer ', ' programmer '],
    defaults: {
      summary: 'Builds and maintains the company’s software to an agreed specification.',
      department: 'Engineering',
      reportsTo: 'Engineering Lead',
      responsibilities: [
        'Design, build and test features to the agreed specification.',
        'Review colleagues’ work and act on review of your own.',
        'Fix defects in what you have built, and keep what you ship supportable.',
        ...COMMON_RESPONSIBILITIES,
      ],
      kpis: [
        'Agreed work is delivered on time and to specification.',
        'Defects found after release, and how quickly they are resolved.',
        'Code is reviewed before it ships.',
      ],
    },
  },
  {
    match: [' designer ', ' design '],
    defaults: {
      summary: 'Designs the company’s products and materials to an agreed brief.',
      department: 'Design',
      reportsTo: 'Design Lead',
      responsibilities: [
        'Produce designs to the agreed brief and revise them on feedback.',
        'Keep work consistent with the company’s visual standards.',
        ...COMMON_RESPONSIBILITIES,
      ],
      kpis: [
        'Work is delivered to brief and on time.',
        'Revisions needed after sign-off.',
      ],
    },
  },
  {
    match: [' sales ', ' business development ', ' account '],
    defaults: {
      summary: 'Wins and keeps customers, and represents the company to them.',
      department: 'Commercial',
      reportsTo: 'Commercial Lead',
      responsibilities: [
        'Find, qualify and pursue opportunities.',
        'Keep the record of every account and conversation current.',
        'Hand over to delivery cleanly once an agreement is signed.',
        ...COMMON_RESPONSIBILITIES,
      ],
      kpis: [
        'Value of business closed against target.',
        'Accuracy of the pipeline record.',
      ],
    },
  },
  {
    match: [' hr ', ' human resource ', ' people '],
    defaults: {
      summary: 'Runs the company’s people processes and keeps its employment records in order.',
      department: 'People',
      reportsTo: 'Managing Director',
      responsibilities: [
        'Run recruitment, onboarding and offboarding end to end.',
        'Keep employment records, contracts and leave balances accurate.',
        'Administer payroll inputs and resolve queries on them.',
        ...COMMON_RESPONSIBILITIES,
      ],
      kpis: [
        'Every employee has a signed contract on file.',
        'Payroll is produced accurately and on time.',
      ],
    },
  },
];

/** Titles that carry responsibility for other people. */
export const isLeadTitle = (designation: string): boolean =>
  /\b(lead|head|manager|chief|director|supervisor)\b/i.test(designation);

export const defaultsForDesignation = (designation: string): RoleDefaults => {
  const needle = ` ${designation.toLowerCase()} `;
  const hit = LIBRARY.find((entry) => entry.match.some((word) => needle.includes(word)));
  const base = hit ? hit.defaults : GENERIC;

  if (!isLeadTitle(designation)) return base;
  if (base.kpis.some((k) => k === MANAGER_KPIS[0])) return base;
  return {
    ...base,
    responsibilities: [
      ...base.responsibilities.filter((r) => !COMMON_RESPONSIBILITIES.includes(r)),
      ...LEAD_RESPONSIBILITIES,
      ...COMMON_RESPONSIBILITIES,
    ],
    kpis: [...base.kpis, ...MANAGER_KPIS],
  };
};

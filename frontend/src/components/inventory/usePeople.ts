import { useEffect, useState } from 'react';
import { orgService, type OrgNode } from '../../services/analyticsService';

export interface Person { id: number; name: string; detail?: string }

const flatten = (nodes: OrgNode[], out: Person[] = []): Person[] => {
  for (const node of nodes) {
    out.push({
      id: node.id,
      name: node.full_name,
      detail: [node.designation, node.department].filter(Boolean).join(' · ') || undefined,
    });
    if (node.reports?.length) flatten(node.reports, out);
  }
  return out;
};

// One request per session, shared by every picker on every screen.
let peopleOnce: Promise<Person[]> | null = null;

/**
 * Everybody, for a "hand it to whom?" picker.
 *
 * Deliberately the org chart rather than the user list: transferring an item
 * is something its *holder* may do, and a plain employee cannot read
 * `GET /users/` at all — employeeService.getEmployees() returns an empty array
 * for them by design. The org chart is the one people-directory this portal
 * already shows everyone.
 */
export const usePeople = (): Person[] => {
  const [people, setPeople] = useState<Person[]>([]);

  useEffect(() => {
    peopleOnce = peopleOnce ?? orgService.chart()
      .then((roots) => flatten(roots).sort((a, b) => a.name.localeCompare(b.name)))
      .catch((err) => { peopleOnce = null; throw err; });
    let live = true;
    peopleOnce.then((list) => { if (live) setPeople(list); }).catch(() => { if (live) setPeople([]); });
    return () => { live = false; };
  }, []);

  return people;
};

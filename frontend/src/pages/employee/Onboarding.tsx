import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FiCheck, FiChevronRight, FiClock, FiFileText, FiSlash, FiUsers } from 'react-icons/fi';
import {
  lifecycleService, type OnboardingPacket, type OnboardingTask, type TaskStatus,
} from '../../services/lifecycleService';
import { useAuthStore } from '../../store/authStore';
import { isPrivileged } from '../../utils/permissions';
import { ROUTES } from '../../config/constants';
import './Onboarding.css';

const KIND_COPY: Record<string, { label: string; hint: string }> = {
  document: { label: 'Document', hint: 'Completes itself once you sign it' },
  data: { label: 'Details', hint: 'HR marks this done once your details are in' },
  action: { label: 'Action', hint: 'HR marks this done' },
};

/**
 * The new joiner's first screen.
 *
 * Document tasks are deliberately not tickable: marking one done by hand would
 * record a signature that never happened, and the server refuses it with a
 * 400. They carry a link to the document instead, and complete themselves when
 * it is signed.
 */
const Onboarding = () => {
  const { user } = useAuthStore();
  const canManage = isPrivileged(user);

  const [packet, setPacket] = useState<OnboardingPacket | null>(null);
  const [everyone, setEveryone] = useState<OnboardingPacket[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      // null, not a 404, when there is no packet — that is "nothing to show",
      // not a failure.
      const [mine, all] = await Promise.all([
        lifecycleService.myOnboarding(),
        // The endpoint is admin/HR only, so it is only asked for by someone
        // the server would answer.
        canManage ? lifecycleService.allOnboarding() : Promise.resolve([]),
      ]);
      setPacket(mine);
      setEveryone(all);
    } catch {
      setError('Your onboarding checklist could not be loaded. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }, [canManage]);

  useEffect(() => { void load(); }, [load]);

  if (isLoading) return <div className="ob-page"><div className="ob-card ob-empty">Loading your checklist…</div></div>;

  if (error) {
    return <div className="ob-page"><div className="ob-card ob-error" role="alert">{error}</div></div>;
  }

  const remaining = packet ? packet.total - packet.done : 0;

  return (
    <div className="ob-page">
      <header className="ob-head">
        <div>
          <h1>
            {packet
              ? `Welcome${packet.employee_name ? `, ${packet.employee_name.split(' ')[0]}` : ''}`
              : 'Onboarding'}
          </h1>
          <p>
            {packet
              ? 'A few things to get you set up. They can be done in any order.'
              : 'Checklists for new joiners.'}
          </p>
        </div>
      </header>

      {packet ? (
        <>
          <section className="ob-card ob-progress" aria-label="Checklist progress">
            <div className="ob-progress-top">
              <div>
                <strong>{packet.done} of {packet.total} done</strong>
                <span>
                  {packet.completed_at
                    ? 'Your onboarding is complete.'
                    : `${remaining} item${remaining === 1 ? '' : 's'} left`}
                </span>
              </div>
              <em>{packet.percent_complete}%</em>
            </div>
            <div className="ob-track" role="img" aria-label={`${packet.percent_complete}% complete`}>
              <span style={{ width: `${Math.min(100, Math.max(0, packet.percent_complete))}%` }} />
            </div>
          </section>

          <ul className="ob-list">
            {packet.tasks.map((task) => <TaskRow key={task.id} task={task} />)}
          </ul>
        </>
      ) : !canManage ? (
        <div className="ob-card ob-empty">
          <FiCheck size={18} /> You have no onboarding checklist. Nothing to do here.
        </div>
      ) : null}

      {canManage && (
        <ManagePackets
          packets={everyone}
          onChanged={() => { void load(); }}
          onError={setError}
        />
      )}
    </div>
  );
};

/* --------------------------- HR: mark tasks done ----------------------- */

/**
 * Where HR marks the steps only they can see happen — the laptop handed over,
 * the details supplied. Document tasks are the exception: they complete
 * themselves on signature and the server refuses to have one ticked by hand,
 * so those offer Waive alone.
 */
const ManagePackets = ({ packets, onChanged, onError }: {
  packets: OnboardingPacket[];
  onChanged: () => void;
  onError: (text: string) => void;
}) => {
  const [openId, setOpenId] = useState<number | null>(null);
  const [busyTask, setBusyTask] = useState<number | null>(null);

  const set = async (taskId: number, status: TaskStatus) => {
    setBusyTask(taskId);
    try {
      await lifecycleService.updateTask(taskId, { status });
      onChanged();
    } catch (err: any) {
      onError(err?.message || 'That task could not be updated.');
    } finally {
      setBusyTask(null);
    }
  };

  if (!packets.length) {
    return (
      <section className="ob-manage">
        <h2><FiUsers size={16} /> New joiners</h2>
        <div className="ob-card ob-empty">No onboarding checklists have been started.</div>
      </section>
    );
  }

  return (
    <section className="ob-manage">
      <h2><FiUsers size={16} /> New joiners</h2>
      {packets.map((p) => {
        const open = openId === p.id;
        return (
          <div key={p.id} className="ob-card ob-packet">
            <button className="ob-packet-head" onClick={() => setOpenId(open ? null : p.id)}>
              <span className="ob-packet-name">{p.employee_name || `Employee #${p.employee_id}`}</span>
              <span className="ob-packet-count">{p.done}/{p.total}</span>
              <span className="ob-packet-pct">{p.percent_complete}%</span>
              <FiChevronRight size={15} className={open ? 'is-open' : ''} />
            </button>

            {open && (
              <div className="ob-packet-tasks">
                {p.tasks.map((task) => (
                  <div key={task.id} className="ob-manage-row">
                    <span className="ob-manage-label">
                      {task.label}
                      <small>{task.kind}</small>
                    </span>
                    <span className={`ob-manage-state is-${task.status}`}>{task.status}</span>
                    <div className="ob-manage-actions">
                      {task.kind !== 'document' && task.status !== 'completed' && (
                        <button disabled={busyTask === task.id} onClick={() => set(task.id, 'completed')}>
                          Mark done
                        </button>
                      )}
                      {task.status !== 'waived' && (
                        <button disabled={busyTask === task.id} onClick={() => set(task.id, 'waived')}>
                          Waive
                        </button>
                      )}
                      {task.status !== 'pending' && (
                        <button disabled={busyTask === task.id} onClick={() => set(task.id, 'pending')}>
                          Reopen
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
};

const TaskRow = ({ task }: { task: OnboardingTask }) => {
  const done = task.status === 'completed';
  const waived = task.status === 'waived';
  const kind = KIND_COPY[task.kind] || { label: task.kind, hint: '' };

  return (
    <li className={`ob-card ob-task${done ? ' is-done' : ''}${waived ? ' is-waived' : ''}`}>
      <span className="ob-tick" aria-hidden="true">
        {done ? <FiCheck size={15} /> : waived ? <FiSlash size={14} /> : <FiClock size={14} />}
      </span>

      <div className="ob-task-body">
        <h3>{task.label}</h3>
        {task.detail && <p>{task.detail}</p>}
        <span className="ob-kind">
          {kind.label}
          {!done && !waived && kind.hint ? ` · ${kind.hint}` : ''}
          {waived ? ' · Waived by HR' : ''}
        </span>
      </div>

      {/* Document tasks link to the document rather than offering a tick: the
          signature is what completes them, and the server rejects any attempt
          to mark one done by hand. */}
      {task.kind === 'document' && task.employee_document_id && !done && !waived && (
        <Link className="ob-open" to={`${ROUTES.DOCUMENTS}?doc=${task.employee_document_id}`}>
          <FiFileText size={14} /> Read and sign <FiChevronRight size={14} />
        </Link>
      )}
    </li>
  );
};

export default Onboarding;

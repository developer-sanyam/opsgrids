/**
 * Representative (non-live) data for the surfaces with no service yet: the headcount-trend
 * history, the recent-activity timeline, and today's task list. Every widget that reads from
 * here renders the shared `SampleChip` so a sample number is never mistaken for a live one.
 */
import type { ActivityItem, TaskItem, TrendPoint, TrendRange } from "@/api/types";

/** Headcount over time — a representative growth curve per range. */
export const HEADCOUNT_TREND: Record<TrendRange, TrendPoint[]> = {
  monthly: [
    { label: "Jan", value: 18 },
    { label: "Feb", value: 19 },
    { label: "Mar", value: 19 },
    { label: "Apr", value: 20 },
    { label: "May", value: 21 },
    { label: "Jun", value: 21 },
    { label: "Jul", value: 22 },
    { label: "Aug", value: 24 },
    { label: "Sep", value: 24 },
    { label: "Oct", value: 25 },
    { label: "Nov", value: 26 },
    { label: "Dec", value: 27 },
  ],
  quarterly: [
    { label: "Q1", value: 19 },
    { label: "Q2", value: 21 },
    { label: "Q3", value: 24 },
    { label: "Q4", value: 27 },
  ],
  yearly: [
    { label: "2022", value: 11 },
    { label: "2023", value: 16 },
    { label: "2024", value: 20 },
    { label: "2025", value: 23 },
    { label: "2026", value: 27 },
  ],
};

/** Percent change to show next to the trend headline, per range (representative). */
export const TREND_DELTA: Record<TrendRange, number> = {
  monthly: 5.2,
  quarterly: 9.1,
  yearly: 17.4,
};

/** A representative attendance rate for today, and the count out on leave. */
export const SAMPLE_ATTENDANCE = {
  ratePct: 94.2,
  rateDelta: 1.3,
  onLeaveDelta: -1.4,
};

export const RECENT_ACTIVITY: ActivityItem[] = [
  {
    id: "a1",
    actor: "Priya Nair",
    action: "approved leave for",
    target: "Daniel Okafor",
    when: "12 min ago",
    tone: "positive",
  },
  {
    id: "a2",
    actor: "Tobias Berg",
    action: "moved",
    target: "Grace Adeyemi to Onboarding",
    when: "1 hr ago",
    tone: "accent",
  },
  {
    id: "a3",
    actor: "Sneha Kulkarni",
    action: "logged an attendance correction for",
    target: "Kabir Malhotra",
    when: "3 hr ago",
    tone: "info",
  },
  {
    id: "a4",
    actor: "Fatima Sheikh",
    action: "flagged a probation review for",
    target: "Meera Krishnan",
    when: "Yesterday",
    tone: "late",
  },
];

export const TASKS_TODAY: TaskItem[] = [
  { id: "t1", title: "Confirm offer with Lucas Almeida", due: "Due 2:00 PM", done: false },
  { id: "t2", title: "Close August payroll inputs", due: "Due 4:30 PM", done: false },
  { id: "t3", title: "Publish Q3 headcount plan", due: "Done", done: true },
  { id: "t4", title: "Review 3 pending leave requests", due: "Done", done: true },
  { id: "t5", title: "Schedule onboarding for new joiners", due: "Tomorrow", done: false },
];

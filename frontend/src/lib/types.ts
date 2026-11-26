export interface Deal {
  id: string;
  name: string;
  customer_id: string;
  customer_name: string;
  stage: string;
  product: string;
  pipeline: string;
  value: number;
  close_date: string;
  previous_close_date: string | null;
  health: string;
  momentum: string;
  crm_probability: number;
  simulated_probability: number | null;
  simulated_confidence: string | null;
  simulated_assumption: string | null;
  forecast_category: string;
  owner_name: string;
  next_step: string;
  situation: string;
  last_interaction_at: string | null;
  next_interaction_at: string | null;
  region: string;
  business_unit: string;
  data_origin: string;
}

export interface Commitment {
  id: string;
  customer_id: string;
  customer_name: string;
  opportunity_id: string | null;
  opportunity_name: string | null;
  owner_name: string;
  owner_side: string;
  description: string;
  importance: string;
  status: string;
  due_date: string;
  evidence: string;
  related_recommendation: string;
  completion_note: string;
  completed_at: string | null;
}

export interface Approval {
  id: string;
  customer_id: string | null;
  customer_name: string | null;
  opportunity_id: string | null;
  opportunity_name: string | null;
  action_label: string;
  title: string;
  proposed_action: string;
  source_label: string;
  current_value: string;
  proposed_value: string;
  reason: string;
  evidence: string;
  impact: string;
  constraint_note: string;
  status: string;
  revision: number;
  decision_note: string;
  decided_by: string | null;
  result_note: string;
}

export interface TimelineItem {
  id: string;
  occurred_at: string | null;
  kind: string;
  source_system: string;
  source_label: string;
  source_reference?: string;
  title: string;
  summary: string;
  direction: string | null;
}

export interface Intelligence {
  id: string;
  category: string;
  summary: string;
  producer: string;
  simulated: boolean;
}

export interface FilterOptions {
  owners: { id: string; name: string }[];
  customers: { id: string; name: string }[];
  opportunities: { id: string; name: string }[];
  pipelines: string[];
  products: string[];
  stages: string[];
  regions: string[];
  business_units: string[];
}

export interface Meta {
  today: string;
  date_label: string;
  disclaimer: string;
  user: { id: string; name: string; email: string; role: string };
  organisation: { id: string; name: string };
  counts: { pending_approvals: number; overdue_commitments: number };
}

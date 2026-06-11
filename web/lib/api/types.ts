export type QueueSummary = {
  pending: number;
  running: number;
  done: number;
  failed: number;
};

export type Challenge = {
  id: string;
  title: string;
  category: string;
  difficulty: string;
  runtime: string;
  framework: string;
  build_status: string;
  solve_status: string;
  path: string;
  updated: string;
};

export type ProgressSnapshot = {
  shard: string;
  challenge_id: string;
  worker: string;
  stage: string;
  status: string;
  percent: number;
  message: string;
  updated_at: string;
};

export type ProgressEvent = {
  id: number;
  shard: string;
  challenge_id: string;
  worker: string;
  stage: string;
  status: string;
  percent: number;
  message: string;
  created_at: string;
};

export type Shard = {
  name: string;
  state: string;
  count: number;
  categories: string[];
  updated: string;
};

export type Seed = {
  id: string;
  title: string;
  category: string;
  difficulty: string;
  points: number;
  primary_technique: string;
  learning_objective: string;
  runtime?: string;
  framework?: string;
  port?: number;
};

export type LogFile = {
  name: string;
  size: number;
  updated: string;
};

export type DashboardState = {
  summary: {
    challenges: number;
    validated: number;
    built: number;
    queue: QueueSummary;
    categories: Record<string, number>;
  };
  challenges: Challenge[];
  seeds: Seed[];
  shards: Shard[];
  logs: LogFile[];
  validation: unknown;
  process: {
    running: boolean;
    kind?: string;
    message?: string;
  };
  progress: {
    snapshots: ProgressSnapshot[];
    events: ProgressEvent[];
    storage: {
      path: string;
      fallback: boolean;
      warning: string;
    };
  };
  updated_at: string;
};

export type TraceEvent = {
  worker: string;
  shard: string;
  stage: string;
  status: string;
  message?: string;
  file?: string;
  tool?: string;
  log?: string;
  ts: number;
};

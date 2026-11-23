// Shapes of the platform API responses used by the screens. Many endpoints return documented
// JSON objects (typed as `dict` in OpenAPI), so the fields the UI reads are declared here.

export type Pillars = Record<"MOMENTUM" | "QUALITY" | "GROWTH" | "VALUATION" | "RISK", number | null>;

export type Job = {
  id: string; kind: "SCAN" | "OPTIMIZE" | "BACKTEST" | "TRAIN"; status: string; created_at: string;
  as_of: string | null; universe_snapshot_id: string | null; progress: number; total_items: number;
  done_items: number; failed_items: number; summary: Record<string, any>; versions?: Record<string, string>;
  error?: string | null; params?: Record<string, any>; status_url?: string;
};

export const RUNNING = (j: { status: string }) => j.status === "QUEUED" || j.status === "RUNNING";

export type Decision = {
  instrument_id: string; symbol: string; exchange: string; name: string | null; industry: string | null;
  route: string; rank: number; state: string; holding_state: string | null; coverage: string;
  opportunity: number | null; uncertainty: number | null; pillars: Pillars; supporting: string[];
  counter: string[]; overrides: string[]; explanation: string; as_of: string;
  features?: Record<string, number | null>; freshness?: Record<string, any>;
};

export type Results = {
  job: { id: string; status: string; as_of: string; versions: Record<string, string> };
  summary: Record<string, any>; total: number; next_cursor: string | null; data: Decision[];
};

export type Universe = { id: string; name: string; kind: string; params: Record<string, any>; created_at: string };

export type Portfolio = { id: string; name: string; base_currency: string; is_paper: boolean;
  max_position_weight: number; created_at: string };

export type Position = { instrument_id: string; symbol: string; exchange: string; route: string;
  quantity: number; average_cost: number; cost: number; price: number | null; price_date: string | null;
  market_value: number | null; unrealized_pnl: number | null; realized_pnl: number; dividends: number;
  weight: number | null };

export type Holdings = { portfolio: { id: string; name: string; is_paper: boolean }; as_of: string;
  nav: number; invested: number; cash: number; fees: number; external_flows: number; realized_pnl: number;
  positions: Position[]; unpriced: any[]; warnings: string[] };

export type Tx = { id: string; seq: number; trade_date: string; type: string; symbol: string | null;
  exchange: string | null; quantity: number; price: number; fees: number; amount: number;
  notes: string | null; source: string; voided: boolean; void_reason: string | null };

export type Level = { price: number | null; state: string };
export type PriceLevels = { current_price: number | null; stop_loss: Level; target_price: Level;
  trailing_stop_loss: Level };

export type Plan = { id: string; client_ref: string; status: string; version: number; side: string;
  symbol: string; exchange: string; route: string; price_levels: PriceLevels; legs: Record<string, any>;
  explain?: Record<string, any>; events?: any[]; portfolio_id?: string | null; quantity?: number | null;
  created_at?: string; evaluated_through?: string | null };

export type Family = { code: string; name: string; status: string; required_data: string[];
  implementation: string; validation: string; limitations: string; blocked_by: string[];
  signals: string[]; default_config: Record<string, any> };

export type MlModel = { id: string; name: string; version: number; stage: string; kind: string;
  horizon: number; created_at: string; gates?: Record<string, any>; metrics?: Record<string, any>;
  [k: string]: any };

export type PaperOrder = { id: string; client_order_id: string; symbol: string; exchange: string;
  side: "BUY" | "SELL"; order_type: string; quantity: number; limit_price: number | null;
  stop_price: number | null; tif: string; status: string; filled_quantity: number;
  average_price: number | null; fees: number; placed_on: string; simulated_through: string | null;
  reason: string | null; venue: string; events?: { at: string; from: string | null; to: string | null; note?: string }[] };

export type AlertRule = { id: string; name: string; kind: string; symbol: string; exchange: string;
  threshold: number; cooldown_days: number; channels: string[]; enabled: boolean;
  last_triggered_on: string | null };

export type FeedItem = { id: string; source: string; kind: string; symbol: string; bar_date: string;
  message: string; value: number | null; route: string; acknowledged: boolean; created_at: string };

export type IndexCard = { index_id: string; name?: string; status: string; as_of?: string; close?: number;
  change_1d?: number | null; change_1m?: number | null; change_1y?: number | null; change_ytd?: number | null;
  high_52w?: number; low_52w?: number; pe?: number | null; pb?: number | null; dividend_yield?: number | null;
  spark?: { d: string; c: number }[]; message?: string };

export type Overview = { lake_release: string; lake_status: string; latest_market_date: string;
  calendar_days_since: number; freshness: string; note: string; indices: IndexCard[] };

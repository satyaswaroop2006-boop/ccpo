-- ============================================================================
-- Credit Card Portfolio Optimiser — Migration 0001_init.sql
-- PostgreSQL 15+ / Supabase
-- Implements Part D. All vocabulary CHECKs mirror Part C's schema constructs.
-- ============================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- 1. CATALOG — issuers, cards, currencies
-- ---------------------------------------------------------------------------

create table issuers (
  id            uuid primary key default gen_random_uuid(),
  key           text not null unique,
  name          text not null,
  issuer_type   text not null check (issuer_type in ('bank','nbfc','network_issuer')),
  website       text,
  support_url   text,
  created_at    timestamptz not null default now()
);

create table cards (
  id                    uuid primary key default gen_random_uuid(),
  issuer_id             uuid not null references issuers(id),
  key                   text not null unique,          -- e.g. 'hdfc_infinia'
  name                  text not null,
  variant               text,
  network               text not null check (network in
                          ('visa','mastercard','rupay','amex','diners')),
  tier                  text check (tier in
                          ('entry','core','premium','super_premium','invite_only')),
  segment               text,                          -- cashback | travel | fuel | upi | ...
  official_product_url  text,
  application_url       text,
  launch_date           date,
  discontinue_date      date,
  active                boolean not null default true,
  created_at            timestamptz not null default now()
);
create index idx_cards_issuer on cards(issuer_id);

create table reward_currencies (
  id             uuid primary key default gen_random_uuid(),
  key            text not null unique,                 -- 'cashback_inr', 'hdfc_rp'
  name           text not null,
  issuer_id      uuid references issuers(id),
  expiry_months  int,                                  -- null = no expiry
  created_at     timestamptz not null default now()
);

create table redemption_routes (
  id                   uuid primary key default gen_random_uuid(),
  currency_id          uuid not null references reward_currencies(id),
  key                  text not null,
  route_type           text not null check (route_type in
                         ('cash','statement_credit','voucher','travel_portal','transfer')),
  ratio                numeric(12,6) not null,          -- ₹ per point on this route
  friction_default     numeric(4,3) not null default 1.0
                         check (friction_default > 0 and friction_default <= 1),
  min_points           int,
  per_point_fee        numeric(12,6) not null default 0,
  flat_redemption_fee  numeric(12,2) not null default 0,
  transfer_partner     text,                            -- airline/hotel programme key
  transfer_ratio       numeric(8,4),                    -- source pts per partner pt
  partner_point_value  numeric(12,6),                   -- est. ₹ per partner point
  notes                text,
  unique (currency_id, key)
);
create index idx_routes_currency on redemption_routes(currency_id);

-- ---------------------------------------------------------------------------
-- 2. CATALOG — versioned rule bundles (the Part C objects)
--    All child tables hang off card_versions; published bundles are immutable.
-- ---------------------------------------------------------------------------

create table card_versions (
  id              uuid primary key default gen_random_uuid(),
  card_id         uuid not null references cards(id),
  version_no      int  not null,
  status          text not null default 'draft'
                    check (status in ('draft','published','deprecated')),
  effective_from  date not null,
  effective_to    date,                                 -- null = open-ended
  joining_fee     numeric(12,2) not null default 0,
  annual_fee      numeric(12,2) not null default 0,
  gst_rate        numeric(5,4)  not null default 0.18,
  forex_markup    numeric(6,5)  not null default 0.035,
  currency_id     uuid not null references reward_currencies(id),
  notes           text,
  created_at      timestamptz not null default now(),
  published_at    timestamptz,
  unique (card_id, version_no),
  check (effective_to is null or effective_to >= effective_from)
);
create index idx_cv_card_status on card_versions(card_id, status);
create index idx_cv_published on card_versions(card_id, effective_from, effective_to)
  where status = 'published';

create table caps (
  id               uuid primary key default gen_random_uuid(),
  card_version_id  uuid not null references card_versions(id) on delete cascade,
  key              text not null,
  measure          text not null check (measure in ('reward','spend')),
  amount           numeric(14,2) not null check (amount > 0),
  window_def       jsonb not null,        -- C.2.4 Window object
  scope            text not null default 'rule',  -- 'rule' | 'rule_group:<key>' | 'card'
  overflow         text not null default 'base_rate'
                     check (overflow in ('base_rate','zero')),
  unique (card_version_id, key)
);
create index idx_caps_cv on caps(card_version_id);

create table earning_rules (
  id               uuid primary key default gen_random_uuid(),
  card_version_id  uuid not null references card_versions(id) on delete cascade,
  key              text not null,
  selector         jsonb not null default '{}',   -- C.2.1
  accrual          jsonb not null,                -- C.2.2
  rule_group       text,
  priority         int not null default 10,
  stacks_with_base boolean not null default false,
  requires_activation boolean not null default false,  -- dormant until an
                       -- activate_rule threshold payload fires (C.3)
  unique (card_version_id, key)
);
create index idx_er_cv on earning_rules(card_version_id);

create table earning_rule_caps (
  earning_rule_id uuid not null references earning_rules(id) on delete cascade,
  cap_id          uuid not null references caps(id) on delete cascade,
  primary key (earning_rule_id, cap_id)
);

create table thresholds (
  id               uuid primary key default gen_random_uuid(),
  card_version_id  uuid not null references card_versions(id) on delete cascade,
  key              text not null,
  basis            jsonb not null,   -- {measure, selector_override, window} — C.3
  tier_mode        text not null default 'cumulative'
                     check (tier_mode in ('cumulative','highest_only')),
  unique (card_version_id, key)
);
create index idx_th_cv on thresholds(card_version_id);

create table threshold_tiers (
  id               uuid primary key default gen_random_uuid(),
  threshold_id     uuid not null references thresholds(id) on delete cascade,
  tier_index       int not null,
  threshold_amount numeric(14,2) not null check (threshold_amount > 0),
  payload          jsonb not null,   -- typed payload — C.3 table
  unique (threshold_id, tier_index)
);

create table exclusions (
  id               uuid primary key default gen_random_uuid(),
  card_version_id  uuid not null references card_versions(id) on delete cascade,
  key              text not null,
  selector         jsonb not null,
  excluded_from    text[] not null
                     check (excluded_from <@ array['rewards','milestones','fee_waiver']
                            and array_length(excluded_from,1) >= 1),
  note             text,
  unique (card_version_id, key)
);
create index idx_ex_cv on exclusions(card_version_id);

create table benefits (
  id                          uuid primary key default gen_random_uuid(),
  card_version_id             uuid not null references card_versions(id) on delete cascade,
  key                         text not null,
  kind                        text not null check (kind in ('countable','flat_perk','voucher')),
  unit_label                  text,
  entitlement                 numeric(10,2),           -- countable quota
  entitlement_window          jsonb,                    -- Window object
  qualification_threshold_id  uuid references thresholds(id),
  face_value                  numeric(12,2),            -- vouchers / flat perks
  expiry_days                 int,
  value_ref                   text,                     -- assumptions-registry key
  utilisation_ref             text,                     -- user-profile key
  friction_ref                text,
  guest_charge                numeric(10,2) not null default 0,
  supplementary_included      boolean not null default false,
  unique (card_version_id, key)
);
create index idx_ben_cv on benefits(card_version_id);

create table surcharges (
  id               uuid primary key default gen_random_uuid(),
  card_version_id  uuid not null references card_versions(id) on delete cascade,
  key              text not null,
  selector         jsonb not null,
  rate             numeric(6,5) not null,
  gst_on_surcharge numeric(5,4) not null default 0.18,
  unique (card_version_id, key)
);
create index idx_sur_cv on surcharges(card_version_id);

-- Live-version resolver
create view current_card_versions as
  select cv.*
  from card_versions cv
  where cv.status = 'published'
    and cv.effective_from <= current_date
    and (cv.effective_to is null or cv.effective_to >= current_date);

-- ---------------------------------------------------------------------------
-- 3. IMMUTABILITY TRIGGERS  (Part D, Decision 2)
-- ---------------------------------------------------------------------------

create or replace function forbid_published_mutation() returns trigger
language plpgsql as $$
begin
  if tg_op = 'DELETE' then
    if old.status = 'published' then
      raise exception 'card_version % is published and cannot be deleted', old.id;
    end if;
    return old;
  end if;
  if old.status = 'published' then
    -- permitted mutations only: deprecate, and/or close effective_to
    if new.status not in ('published','deprecated') then
      raise exception 'published card_version % may only move to deprecated', old.id;
    end if;
    if (to_jsonb(new) - 'status' - 'effective_to')
       is distinct from (to_jsonb(old) - 'status' - 'effective_to') then
      raise exception 'published card_version % is immutable (only status/effective_to may change)', old.id;
    end if;
  end if;
  return new;
end $$;

create trigger trg_cv_immutable
  before update or delete on card_versions
  for each row execute function forbid_published_mutation();

create or replace function forbid_child_mutation() returns trigger
language plpgsql as $$
declare v_status text; v_cv uuid;
begin
  v_cv := coalesce(new.card_version_id, old.card_version_id);
  select status into v_status from card_versions where id = v_cv;
  if v_status = 'published' then
    raise exception 'rules of published card_version % are immutable — create a new version', v_cv;
  end if;
  return coalesce(new, old);
end $$;

-- attach to every rule table
do $$
declare t text;
begin
  foreach t in array array['caps','earning_rules','thresholds','exclusions','benefits','surcharges']
  loop
    execute format(
      'create trigger trg_%s_immutable before insert or update or delete on %I
       for each row execute function forbid_child_mutation()', t, t);
  end loop;
end $$;
-- (threshold_tiers guarded transitively: parent thresholds row cannot change,
--  and tiers carry no card_version_id; direct guard added via parent lookup)

create or replace function forbid_tier_mutation() returns trigger
language plpgsql as $$
declare v_status text;
begin
  select cv.status into v_status
  from thresholds th join card_versions cv on cv.id = th.card_version_id
  where th.id = coalesce(new.threshold_id, old.threshold_id);
  if v_status = 'published' then
    raise exception 'tiers of a published card_version are immutable';
  end if;
  return coalesce(new, old);
end $$;

create trigger trg_tiers_immutable
  before insert or update or delete on threshold_tiers
  for each row execute function forbid_tier_mutation();

-- ---------------------------------------------------------------------------
-- 4. SOURCES & PROVENANCE  (§4; Part D, Decision 4)
-- ---------------------------------------------------------------------------

create table sources (
  id              uuid primary key default gen_random_uuid(),
  url             text not null,
  source_type     text not null check (source_type in
                    ('product_page','reward_terms','mitc','fee_schedule','faq',
                     'network_benefits','official_pdf','transfer_partner_doc','third_party')),
  issuer_id       uuid references issuers(id),
  title           text,
  captured_at     timestamptz,
  last_checked_at timestamptz,
  storage_path    text,            -- Supabase Storage snapshot of the page/PDF
  evidence_notes  text,
  created_at      timestamptz not null default now()
);
create index idx_sources_issuer on sources(issuer_id);

create table source_links (
  id               uuid primary key default gen_random_uuid(),
  source_id        uuid not null references sources(id),
  entity_type      text not null check (entity_type in
                     ('card_version','earning_rule','threshold','cap','exclusion',
                      'benefit','surcharge','redemption_route','reward_currency')),
  entity_id        uuid not null,   -- soft reference; nightly orphan check
  confidence       text not null default 'medium'
                     check (confidence in ('high','medium','low')),
  reviewer_status  text not null default 'unreviewed'
                     check (reviewer_status in ('unreviewed','approved','rejected')),
  effective_from   date,
  effective_to     date,
  previous_rule_note text,          -- what this superseded, for devaluation history
  created_at       timestamptz not null default now()
);
create index idx_slinks_entity on source_links(entity_type, entity_id);
create index idx_slinks_source on source_links(source_id);
create index idx_slinks_review on source_links(reviewer_status)
  where reviewer_status = 'unreviewed';

create table assumption_versions (
  id           uuid primary key default gen_random_uuid(),
  version_no   int not null unique,
  status       text not null default 'draft'
                 check (status in ('draft','published','deprecated')),
  snapshot     jsonb not null,      -- full C.7 registry document
  published_at timestamptz,
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 5. USER DOMAIN  (RLS-protected)
-- ---------------------------------------------------------------------------

create table user_profiles (
  user_id              uuid primary key references auth.users(id) on delete cascade,
  display_name         text,
  city                 text,
  complexity_tolerance text not null default 'moderate'
                         check (complexity_tolerance in
                           ('simple','moderate','enthusiast','power_user')),
  created_at           timestamptz not null default now()
);

create table user_preferences (
  user_id            uuid primary key references auth.users(id) on delete cascade,
  redemption_prefs   jsonb not null default '{}',  -- currency_key -> route_key (v_exp basis)
  benefit_needs      jsonb not null default '{}',  -- lounge visits, movies, values (A.8)
  constraints_doc    jsonb not null default '{}',  -- fee budget, networks_required, etc.
  complexity_penalty numeric(10,2) not null default 0,  -- λ; default 0 per C.8
  updated_at         timestamptz not null default now()
);

create table user_spend_profiles (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users(id) on delete cascade,
  label      text not null default 'Default',
  mode       text not null default 'annual' check (mode in ('annual','monthly')),
  is_active  boolean not null default true,
  created_at timestamptz not null default now()
);
create index idx_usp_user on user_spend_profiles(user_id);

create table user_spend_items (
  id                  uuid primary key default gen_random_uuid(),
  spend_profile_id    uuid not null references user_spend_profiles(id) on delete cascade,
  category            text not null,
  channel             text,             -- null = unspecified; 'upi' for aggregate UPI input
  annual_amount       numeric(14,2) not null check (annual_amount >= 0),
  seasonality         jsonb,            -- 12 fractions; null = uniform
  avg_ticket_override numeric(12,2),
  unique (spend_profile_id, category, channel)
);
create index idx_usi_profile on user_spend_items(spend_profile_id);

create table user_wallet_cards (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null references auth.users(id) on delete cascade,
  card_id               uuid not null references cards(id),
  nickname              text,
  acquisition_date      date,
  anniversary_month     int check (anniversary_month between 1 and 12),
  statement_day         int check (statement_day between 1 and 31),
  fee_override          numeric(12,2),   -- actual fee paid; null = card_version fee (§62)
  lifetime_free         boolean not null default false,
  must_keep             boolean not null default false,
  refuse_use            boolean not null default false,
  current_year_progress jsonb not null default '{}',  -- per-threshold spend to date (§31)
  reward_balance        jsonb not null default '{}',  -- currency_key -> {points, expiry}
  closed_at             date,
  created_at            timestamptz not null default now(),
  unique (user_id, card_id)
);
create index idx_uwc_user_open on user_wallet_cards(user_id) where closed_at is null;

create table user_transactions (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references auth.users(id) on delete cascade,
  wallet_card_id uuid references user_wallet_cards(id),
  txn_date       date not null,
  amount         numeric(14,2) not null,
  merchant       text,
  mcc            int,
  category       text,
  channel        text,
  geography      text not null default 'domestic'
                   check (geography in ('domestic','international')),
  surcharge      numeric(12,2) not null default 0,
  is_emi         boolean not null default false,
  created_at     timestamptz not null default now()
);
create index idx_utx_user_date on user_transactions(user_id, txn_date);

-- ---------------------------------------------------------------------------
-- 6. COMPUTE DOMAIN — runs, traces, enumeration results
-- ---------------------------------------------------------------------------

create table evaluation_runs (
  id                     uuid primary key default gen_random_uuid(),
  user_id                uuid references auth.users(id) on delete cascade,
  engine_version         text not null,
  spend_profile_id       uuid references user_spend_profiles(id),
  assumptions_version_id uuid not null references assumption_versions(id),
  portfolio              jsonb not null,  -- [{card_version_id, wallet_overrides}], incl. c0
  allocation             jsonb not null,  -- x(c,k,t)
  year_mode              text not null default 'steady_state'
                           check (year_mode in ('year_1','steady_state','three_year')),
  results                jsonb not null,  -- NACV per card, PV, rates, range values
  flags                  jsonb not null default '[]',  -- estimation/approximation flags
  input_hash             text not null,
  created_at             timestamptz not null default now()
);
create index idx_eval_user_time on evaluation_runs(user_id, created_at desc);
create index idx_eval_hash on evaluation_runs(input_hash);

create table evaluation_traces (
  run_id uuid primary key references evaluation_runs(id) on delete cascade,
  trace  jsonb not null               -- C.10 trace nodes
);

create table optimisation_runs (
  id                         uuid primary key default gen_random_uuid(),
  user_id                    uuid references auth.users(id) on delete cascade,
  journey                    text not null check (journey in ('greenfield','wallet')),
  cardinality_mode           text not null default 'up_to'
                               check (cardinality_mode in ('exactly','up_to','optimiser_decides')),
  max_cards                  int,
  constraints_snapshot       jsonb not null default '{}',
  candidate_card_version_ids uuid[] not null,
  spend_profile_id           uuid references user_spend_profiles(id),
  assumptions_version_id     uuid references assumption_versions(id),
  status                     text not null default 'running'
                               check (status in ('running','completed','failed')),
  best_subset_key            text,
  started_at                 timestamptz not null default now(),
  finished_at                timestamptz
);
create index idx_opt_user_time on optimisation_runs(user_id, started_at desc);

create table portfolio_subset_results (
  id                  uuid primary key default gen_random_uuid(),
  optimisation_run_id uuid not null references optimisation_runs(id) on delete cascade,
  subset_key          text not null,     -- sorted card_version uuids, '+'-joined
  size                int  not null,
  pv_planned          numeric(14,2) not null,   -- inner-MILP objective
  pv_exact            numeric(14,2),            -- evaluator value (post repair pass)
  repair_applied      boolean not null default false,
  allocation          jsonb,
  evaluation_run_id   uuid references evaluation_runs(id),
  unique (optimisation_run_id, subset_key)
);
create index idx_psr_frontier on portfolio_subset_results
  (optimisation_run_id, size, pv_exact desc);

-- ---------------------------------------------------------------------------
-- 7. ROW-LEVEL SECURITY
-- ---------------------------------------------------------------------------

-- Catalog: readable by everyone, writable only via service role (no write policies)
do $$
declare t text;
begin
  foreach t in array array[
    'issuers','cards','reward_currencies','redemption_routes','card_versions',
    'caps','earning_rules','earning_rule_caps','thresholds','threshold_tiers',
    'exclusions','benefits','surcharges','sources','source_links','assumption_versions']
  loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'create policy %I_read on %I for select to anon, authenticated using (true)', t, t);
  end loop;
end $$;

-- User + compute: owner-only
do $$
declare t text;
begin
  foreach t in array array[
    'user_profiles','user_preferences','user_spend_profiles','user_spend_items',
    'user_wallet_cards','user_transactions',
    'evaluation_runs','optimisation_runs']
  loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;

create policy up_own  on user_profiles      for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy upref_own on user_preferences for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy usp_own on user_spend_profiles for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy uwc_own on user_wallet_cards  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy utx_own on user_transactions  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy eval_own on evaluation_runs   for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy opt_own  on optimisation_runs for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- child tables scoped through parent
alter table user_spend_items enable row level security;
create policy usi_own on user_spend_items for all
  using (exists (select 1 from user_spend_profiles p
                 where p.id = spend_profile_id and p.user_id = auth.uid()))
  with check (exists (select 1 from user_spend_profiles p
                 where p.id = spend_profile_id and p.user_id = auth.uid()));

alter table evaluation_traces enable row level security;
create policy trace_own on evaluation_traces for select
  using (exists (select 1 from evaluation_runs r
                 where r.id = run_id and r.user_id = auth.uid()));

alter table portfolio_subset_results enable row level security;
create policy psr_own on portfolio_subset_results for select
  using (exists (select 1 from optimisation_runs o
                 where o.id = optimisation_run_id and o.user_id = auth.uid()));
-- inserts to traces/subset_results happen via service role (engine)

-- ============================================================================
-- End of 0001_init.sql
-- ============================================================================

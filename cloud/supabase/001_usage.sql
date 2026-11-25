-- Vedit cloud usage metering. Postgres 15 / Supabase. Run once in the SQL editor.
-- The API server calls the functions below with the secret (service_role) key; clients never write.

create table if not exists public.usage (
  user_id uuid not null,
  month text not null,              -- UTC 'YYYY-MM'
  seconds_used float8 not null default 0,
  primary key (user_id, month)
);

create table if not exists public.limits (
  user_id uuid primary key,
  seconds_limit float8 not null
);

create table if not exists public.charges (
  job_id text primary key,
  user_id uuid not null,
  month text not null,
  seconds float8 not null,
  refunded bool not null default false
);

alter table public.usage enable row level security;
alter table public.limits enable row level security;
alter table public.charges enable row level security;

drop policy if exists usage_select_own on public.usage;
create policy usage_select_own on public.usage for select using (user_id = auth.uid());

drop policy if exists limits_select_own on public.limits;
create policy limits_select_own on public.limits for select using (user_id = auth.uid());
-- charges: no client policy (service_role bypasses RLS).

-- Seconds left this month for a user.
create or replace function public.vedit_remaining(p_uid uuid, p_month text, p_default_limit float8)
returns float8
language sql
security definer
set search_path = public
as $$
  select greatest(
    0::float8,
    coalesce((select seconds_limit from public.limits where user_id = p_uid), p_default_limit)
      - coalesce((select seconds_used from public.usage where user_id = p_uid and month = p_month), 0)
  );
$$;

-- Atomic charge. Returns {"status": "ok"|"limit"|"busy", "remaining": seconds}.
-- Idempotent per job_id. p_global_limit null or <= 0 means no global cap.
create or replace function public.vedit_charge(
  p_uid uuid, p_job_id text, p_seconds float8, p_month text,
  p_default_limit float8, p_global_limit float8
)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_limit float8;
  v_used float8;
  v_left float8;
  v_global float8;
begin
  -- serialise all charges/refunds so the per-user and global checks are atomic
  perform pg_advisory_xact_lock(hashtext('vedit_charge'));

  select seconds_limit into v_limit from public.limits where user_id = p_uid;
  v_limit := coalesce(v_limit, p_default_limit);
  select seconds_used into v_used from public.usage where user_id = p_uid and month = p_month;
  v_used := coalesce(v_used, 0);
  v_left := greatest(0, v_limit - v_used);

  if exists (select 1 from public.charges where job_id = p_job_id) then
    return jsonb_build_object('status', 'ok', 'remaining', v_left);
  end if;

  if p_seconds > v_left then
    return jsonb_build_object('status', 'limit', 'remaining', v_left);
  end if;

  if p_global_limit is not null and p_global_limit > 0 then
    select coalesce(sum(seconds_used), 0) into v_global from public.usage where month = p_month;
    if v_global + p_seconds > p_global_limit then
      return jsonb_build_object('status', 'busy', 'remaining', v_left);
    end if;
  end if;

  insert into public.usage (user_id, month, seconds_used)
  values (p_uid, p_month, p_seconds)
  on conflict (user_id, month) do update set seconds_used = public.usage.seconds_used + excluded.seconds_used;

  insert into public.charges (job_id, user_id, month, seconds)
  values (p_job_id, p_uid, p_month, p_seconds);

  return jsonb_build_object('status', 'ok', 'remaining', v_left - p_seconds);
end;
$$;

-- Give the seconds back to the charge's own month, at most once. Unknown job_id is a no-op.
create or replace function public.vedit_refund(p_job_id text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  c public.charges%rowtype;
begin
  perform pg_advisory_xact_lock(hashtext('vedit_charge'));

  update public.charges set refunded = true
  where job_id = p_job_id and not refunded
  returning * into c;

  if found then
    update public.usage set seconds_used = greatest(0, seconds_used - c.seconds)
    where user_id = c.user_id and month = c.month;
  end if;
end;
$$;

revoke execute on function public.vedit_remaining(uuid, text, float8) from public, anon, authenticated;
revoke execute on function public.vedit_charge(uuid, text, float8, text, float8, float8) from public, anon, authenticated;
revoke execute on function public.vedit_refund(text) from public, anon, authenticated;
grant execute on function public.vedit_remaining(uuid, text, float8) to service_role;
grant execute on function public.vedit_charge(uuid, text, float8, text, float8, float8) to service_role;
grant execute on function public.vedit_refund(text) to service_role;

-- Seconds used and the limit for the month, for the usage meter.
create or replace function public.vedit_status(p_uid uuid, p_month text, p_default_limit float8)
returns jsonb
language sql
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'seconds_used', coalesce((select seconds_used from public.usage where user_id = p_uid and month = p_month), 0),
    'seconds_limit', coalesce((select seconds_limit from public.limits where user_id = p_uid), p_default_limit)
  );
$$;

revoke execute on function public.vedit_status(uuid, text, float8) from public, anon, authenticated;
grant execute on function public.vedit_status(uuid, text, float8) to service_role;

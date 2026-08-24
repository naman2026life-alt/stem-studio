create table public.separation_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'queued' check (status in ('queued', 'processing', 'completed', 'failed')),
  source_path text not null,
  source_name text not null,
  vocals_path text,
  drums_path text,
  instrumental_path text,
  progress integer not null default 0 check (progress between 0 and 100),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index separation_jobs_user_created_idx
  on public.separation_jobs (user_id, created_at desc);

create index separation_jobs_queue_idx
  on public.separation_jobs (created_at)
  where status = 'queued';

create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

revoke execute on function public.set_updated_at() from public, anon, authenticated;

create trigger separation_jobs_set_updated_at
before update on public.separation_jobs
for each row execute function public.set_updated_at();

alter table public.separation_jobs enable row level security;

create policy "Users can read their own separation jobs"
on public.separation_jobs for select
to authenticated
using ((select auth.uid()) = user_id);

create policy "Users can create their own separation jobs"
on public.separation_jobs for insert
to authenticated
with check ((select auth.uid()) = user_id and status = 'queued');

create policy "Users can delete their own separation jobs"
on public.separation_jobs for delete
to authenticated
using ((select auth.uid()) = user_id);

grant select, insert, delete on public.separation_jobs to authenticated;
grant all on public.separation_jobs to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'audio',
  'audio',
  false,
  157286400,
  array['audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/flac', 'audio/aac', 'audio/ogg']
)
on conflict (id) do update set
  public = excluded.public,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

create policy "Users can read their own audio"
on storage.objects for select
to authenticated
using (bucket_id = 'audio' and (storage.foldername(name))[1] = (select auth.uid()::text));

create policy "Users can upload their own audio"
on storage.objects for insert
to authenticated
with check (bucket_id = 'audio' and (storage.foldername(name))[1] = (select auth.uid()::text));

create policy "Users can delete their own audio"
on storage.objects for delete
to authenticated
using (bucket_id = 'audio' and (storage.foldername(name))[1] = (select auth.uid()::text));

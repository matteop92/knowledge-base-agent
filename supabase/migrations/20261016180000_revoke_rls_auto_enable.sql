-- The hosted project has a public.rls_auto_enable() security-definer function
-- created outside these migrations. The security advisor flags it as callable
-- by anon and authenticated through the Data API (lint 0028/0029). It is only
-- needed by its event trigger, which does not need an execute grant, so remove
-- direct execution. Local databases do not have the function, so this is a
-- no-op there.
do $$ declare item record; begin
  for item in
    select p.oid::regprocedure as signature
    from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'rls_auto_enable'
  loop
    execute format('revoke execute on function %s from public, anon, authenticated', item.signature);
    -- Fail the migration instead of silently leaving the function callable.
    if has_function_privilege('anon', item.signature, 'execute')
       or has_function_privilege('authenticated', item.signature, 'execute') then
      raise exception 'revoke had no effect on %', item.signature;
    end if;
  end loop;
end $$;

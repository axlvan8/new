-- MyFirebase Supabase Schema
-- Run this in your Supabase project's SQL Editor (supabase.com -> SQL Editor)

-- 1. Profiles table (extends auth.users)
create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  username text unique not null,
  created_at timestamptz default now()
);

-- 2. Conversations
create table if not exists conversations (
  id bigint generated always as identity primary key,
  is_direct boolean not null default true,
  name text,
  owner_id uuid references auth.users(id) not null,
  created_at timestamptz default now()
);

-- 3. Conversation participants
create table if not exists conversation_participants (
  conversation_id bigint references conversations(id) on delete cascade,
  user_id uuid references auth.users(id) on delete cascade,
  joined_at timestamptz default now(),
  primary key (conversation_id, user_id)
);

-- 4. Messages
create table if not exists messages (
  id bigint generated always as identity primary key,
  conversation_id bigint references conversations(id) on delete cascade,
  sender_id uuid references auth.users(id) not null,
  content text not null,
  created_at timestamptz default now()
);

-- Indexes
create index if not exists idx_messages_conv_created on messages(conversation_id, created_at desc);
create index if not exists idx_conv_participants_user on conversation_participants(user_id);

-- 5. Row Level Security
alter table conversations enable row level security;
alter table conversation_participants enable row level security;
alter table messages enable row level security;
alter table profiles enable row level security;

-- Policies: participants can view conversations they belong to
create policy "participants can view conversations" on conversations
  for select using (
    exists (
      select 1 from conversation_participants cp
      where cp.conversation_id = conversations.id
        and cp.user_id = auth.uid()
    )
  );

create policy "participants can view participants" on conversation_participants
  for select using (
    exists (
      select 1 from conversation_participants cp
      where cp.conversation_id = conversation_participants.conversation_id
        and cp.user_id = auth.uid()
    )
  );

create policy "participants can view messages" on messages
  for select using (
    exists (
      select 1 from conversation_participants cp
      where cp.conversation_id = messages.conversation_id
        and cp.user_id = auth.uid()
    )
  );

-- Policies: users can insert messages they send
create policy "users can insert messages" on messages
  for insert with check (auth.uid() = sender_id);

-- Policies: owners can manage their conversations
create policy "owners can manage conversations" on conversations
  for all using (auth.uid() = owner_id);

-- Policies: participants can be added
create policy "participants can insert participants" on conversation_participants
  for insert with check (auth.uid() = user_id);

-- Policies: profiles
create policy "profiles are viewable by everyone" on profiles for select using (true);
create policy "users can manage own profile" on profiles for all using (auth.uid() = id);

-- 6. Auto-create profile when a user signs up
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, username, raw_user_meta_data)
  values (new.id, new.raw_user_meta_data->>'username', new.raw_user_meta_data);
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();

-- 7. RPC: create or get direct conversation
create or replace function public.create_direct_conversation(other_user_id bigint)
returns bigint as $$
declare
  conv_id bigint;
  me uuid := auth.uid();
begin
  select c.id into conv_id
  from conversations c
  join conversation_participants a on a.conversation_id = c.id and a.user_id = me
  join conversation_participants b on b.conversation_id = c.id and b.user_id = other_user_id::uuid
  where c.is_direct = true
  limit 1;

  if conv_id is not null then
    return conv_id;
  end if;

  insert into conversations (is_direct, owner_id) values (true, me) returning id into conv_id;

  insert into conversation_participants (conversation_id, user_id) values
    (conv_id, me), (conv_id, other_user_id::uuid);

  return conv_id;
end;
$$ language plpgsql security definer;

-- 8. RPC: create group conversation
create or replace function public.create_group_conversation(group_name text, participant_ids bigint[])
returns bigint as $$
declare
  conv_id bigint;
  me uuid := auth.uid();
begin
  insert into conversations (is_direct, name, owner_id) values (false, group_name, me) returning id into conv_id;

  insert into conversation_participants (conversation_id, user_id)
  select conv_id, unnest(participant_ids)::uuid;

  insert into conversation_participants (conversation_id, user_id)
  select conv_id, me
  where not exists (
    select 1 from conversation_participants
    where conversation_id = conv_id and user_id = me
  );

  return conv_id;
end;
$$ language plpgsql security definer;

-- 9. RPC: invite to conversation (owner only)
create or replace function public.invite_to_conversation(conv_id bigint, user_ids bigint[])
returns void as $$
declare
  me uuid := auth.uid();
begin
  if not exists (select 1 from conversations where id = conv_id and owner_id = me) then
    raise exception 'Only the owner can invite';
  end if;

  insert into conversation_participants (conversation_id, user_id)
  select conv_id, unnest(user_ids)::uuid
  on conflict do nothing;
end;
$$ language plpgsql security definer;

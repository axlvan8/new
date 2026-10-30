
import { randomBytes, createHash } from 'node:crypto';
import argon2 from 'argon2';
import { Hono } from 'hono';
import { db } from './db.js';

export const auth = new Hono();

const SESSION_DAYS = 7;

function hashToken(token) {
  return createHash('sha256').update(token).digest('hex');
}

function validUsername(username) {
  return (
    typeof username === 'string' &&
    /^[a-zA-Z0-9_]{3,24}$/.test(username)
  );
}

export async function getSessionUser(c) {
  const token = getCookieToken(c);

  if (!token) return null;

  const result = await db.query(
    `SELECT users.id, users.username
     FROM sessions
     JOIN users ON users.id = sessions.user_id
     WHERE sessions.token_hash = $1
       AND sessions.expires_at > NOW()`,
    [hashToken(token)]
  );

  return result.rows[0] ?? null;
}

function getCookieToken(c) {
  const cookie = c.req.header('Cookie') ?? '';
  const match = cookie.match(/(?:^|;\s*)mf_session=([a-f0-9]{64})(?:;|$)/);
  return match?.[1] ?? null;
}

auth.post('/register', async (c) => {
  try {
    const body = await c.req.json();

    if (!validUsername(body.username)) {
      return c.json({
        error: 'Username must be 3–24 characters using letters, numbers, or underscores',
      }, 400);
    }

    if (
      typeof body.password !== 'string' ||
      body.password.length < 12 ||
      body.password.length > 128
    ) {
      return c.json({
        error: 'Password must be between 12 and 128 characters',
      }, 400);
    }

    const passwordHash = await argon2.hash(body.password);

    const result = await db.query(
      `INSERT INTO users (username, password_hash)
       VALUES ($1, $2)
       RETURNING id, username, created_at`,
      [body.username, passwordHash]
    );

    return c.json({ user: result.rows[0] }, 201);
  } catch (error) {
    if (error.code === '23505') {
      return c.json({ error: 'Username is already registered' }, 409);
    }

    console.error('Registration failed:', error.message);
    return c.json({ error: 'Registration failed' }, 500);
  }
});

auth.post('/login', async (c) => {
  try {
    const body = await c.req.json();

    if (
      typeof body.username !== 'string' ||
      typeof body.password !== 'string'
    ) {
      return c.json({ error: 'Username and password are required' }, 400);
    }

    const result = await db.query(
      'SELECT id, username, password_hash FROM users WHERE username = $1',
      [body.username]
    );

    const user = result.rows[0];

    if (!user || !(await argon2.verify(user.password_hash, body.password))) {
      return c.json({ error: 'Invalid username or password' }, 401);
    }

    const token = randomBytes(32).toString('hex');
    const expiresAt = new Date(
      Date.now() + SESSION_DAYS * 24 * 60 * 60 * 1000
    );

    await db.query(
      `INSERT INTO sessions (user_id, token_hash, expires_at)
       VALUES ($1, $2, $3)`,
      [user.id, hashToken(token), expiresAt]
    );

    c.header(
      'Set-Cookie',
      `mf_session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_DAYS * 24 * 60 * 60}`
    );

    return c.json({
      user: { id: user.id, username: user.username },
      expiresAt,
    });
  } catch (error) {
    console.error('Login failed:', error.message);
    return c.json({ error: 'Login failed' }, 500);
  }
});

auth.post('/logout', async (c) => {
  const token = getCookieToken(c);

  if (token) {
    await db.query(
      'DELETE FROM sessions WHERE token_hash = $1',
      [hashToken(token)]
    );
  }

  c.header(
    'Set-Cookie',
    'mf_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
  );

  return c.json({ loggedOut: true });
});

auth.get('/me', async (c) => {
  try {
    const user = await getSessionUser(c);

    if (!user) {
      return c.json({ error: 'Not authenticated' }, 401);
    }

    return c.json({ user });
  } catch (error) {
    console.error('Session lookup failed:', error.message);
    return c.json({ error: 'Could not check session' }, 500);
  }
});

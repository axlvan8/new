
import { readFile } from 'node:fs/promises';
import { serveStatic } from '@hono/node-server/serve-static';
import 'dotenv/config';
import { auth } from './auth.js';
import { Hono } from 'hono';
import { serve } from '@hono/node-server';
import { db } from './db.js';

const app = new Hono();

app.get('/', async (c) => {
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  return c.html(html);
});

app.route('/api/auth', auth);

//app.get('/', (c) => {
//  return c.json({
//    name: 'MyFirebase',
//    status: 'running',
//    version: '0.1.0',
//  });
//});

app.get('/health', (c) => {
  return c.json({ ok: true });
});

app.get('/api/database', async (c) => {
  try {
    const result = await db.query(
      'SELECT current_database() AS database, current_user AS username'
    );

    return c.json({
      connected: true,
      ...result.rows[0],
    });
  } catch (error) {
    console.error('Database query failed:', error.message);
    return c.json({ connected: false, error: 'Database query failed' }, 500);
  }
});
// List notes
app.get('/api/collections/notes', async (c) => {
  try {
    const result = await db.query(
      'SELECT id, content, created_at FROM notes ORDER BY id DESC'
    );

    return c.json({ data: result.rows });
  } catch (error) {
    console.error('List notes failed:', error.message);
    return c.json({ error: 'Could not list notes' }, 500);
  }
});

// Create a note
app.post('/api/collections/notes', async (c) => {
  try {
    const body = await c.req.json();

    if (typeof body.content !== 'string' || !body.content.trim()) {
      return c.json({ error: 'content must be a non-empty string' }, 400);
    }

    const result = await db.query(
      'INSERT INTO notes (content) VALUES ($1) RETURNING id, content, created_at',
      [body.content.trim()]
    );

    return c.json({ data: result.rows[0] }, 201);
  } catch (error) {
    console.error('Create note failed:', error.message);
    return c.json({ error: 'Could not create note' }, 500);
  }
});

// Get one note
app.get('/api/collections/notes/:id', async (c) => {
  const id = c.req.param('id');

  if (!/^[1-9]\d*$/.test(id)) {
    return c.json({ error: 'Invalid note ID' }, 400);
  }

  try {
    const result = await db.query(
      'SELECT id, content, created_at FROM notes WHERE id = $1',
      [id]
    );

    if (result.rowCount === 0) {
      return c.json({ error: 'Note not found' }, 404);
    }

    return c.json({ data: result.rows[0] });
  } catch (error) {
    console.error('Get note failed:', error.message);
    return c.json({ error: 'Could not get note' }, 500);
  }
});

// Delete one note
app.delete('/api/collections/notes/:id', async (c) => {
  const id = c.req.param('id');

  if (!/^[1-9]\d*$/.test(id)) {
    return c.json({ error: 'Invalid note ID' }, 400);
  }

  try {
    const result = await db.query(
      'DELETE FROM notes WHERE id = $1 RETURNING id',
      [id]
    );

    if (result.rowCount === 0) {
      return c.json({ error: 'Note not found' }, 404);
    }

    return c.json({ deleted: true, id: result.rows[0].id });
  } catch (error) {
    console.error('Delete note failed:', error.message);
    return c.json({ error: 'Could not delete note' }, 500);
  }
});
serve({
  fetch: app.fetch,
  port: 3000,
  hostname: '127.0.0.1',
});

console.log('MyFirebase running at http://127.0.0.1:3000');


import { Hono } from 'hono';
import { db } from './db.js';
import { getSessionUser } from './auth.js';

export const messages = new Hono();

const MAX_MESSAGE_LENGTH = 10000;

async function requireUser(c) {
  const user = await getSessionUser(c);

  if (!user) {
    return null;
  }

  return user;
}

function validId(id) {
  return /^[1-9]\d*$/.test(id);
}

async function isParticipant(conversationId, userId) {
  const result = await db.query(
    `SELECT 1 FROM conversation_participants
     WHERE conversation_id = $1 AND user_id = $2`,
    [conversationId, userId]
  );
  return result.rowCount > 0;
}

async function isOwner(conversationId, userId) {
  const result = await db.query(
    `SELECT owner_id FROM conversations WHERE id = $1`,
    [conversationId]
  );
  return (
    result.rowCount > 0 &&
    Number(result.rows[0].owner_id) === Number(userId)
  );
}

// Create a conversation (direct or group)
messages.post('/conversations', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  try {
    const body = await c.req.json();

    const isDirect = body.is_direct !== false;
    const name = typeof body.name === 'string' ? body.name.trim() : null;
    const participantIds = Array.isArray(body.participant_ids)
      ? body.participant_ids.map(Number).filter(id => Number.isInteger(id) && id > 0)
      : [];

    if (isDirect) {
      if (participantIds.length !== 1) {
        return c.json({ error: 'Direct conversations require exactly one other user' }, 400);
      }

      const otherUserId = participantIds[0];

      if (otherUserId === user.id) {
        return c.json({ error: 'Cannot start a direct conversation with yourself' }, 400);
      }

      const other = await db.query(
        'SELECT id FROM users WHERE id = $1',
        [otherUserId]
      );

      if (!other.rowCount) {
        return c.json({ error: 'User not found' }, 404);
      }

      const client = await db.connect();

      try {
        await client.query('BEGIN');

        const existing = await client.query(
          `SELECT c.id, c.created_at
           FROM conversations c
           JOIN conversation_participants a ON a.conversation_id = c.id AND a.user_id = $1
           JOIN conversation_participants b ON b.conversation_id = c.id AND b.user_id = $2
           WHERE c.is_direct = TRUE
           ORDER BY c.created_at DESC
           LIMIT 1`,
          [user.id, otherUserId]
        );

        if (existing.rowCount) {
          await client.query('COMMIT');
          return c.json({ data: existing.rows[0], existing: true });
        }

        const result = await client.query(
          `INSERT INTO conversations (is_direct, owner_id)
           VALUES (TRUE, $1)
           RETURNING id, created_at`,
          [user.id]
        );

        const conversationId = result.rows[0].id;

        await client.query(
          `INSERT INTO conversation_participants (conversation_id, user_id)
           VALUES ($1, $2), ($1, $3)`,
          [conversationId, user.id, otherUserId]
        );

        await client.query('COMMIT');

        return c.json({
          data: { id: conversationId, created_at: result.rows[0].created_at },
          existing: false,
        }, 201);
      } catch (error) {
        await client.query('ROLLBACK');
        throw error;
      } finally {
        client.release();
      }
    }

    // Group conversation
    if (!name || name.length < 1 || name.length > 100) {
      return c.json({ error: 'Group name must be between 1 and 100 characters' }, 400);
    }

    if (!participantIds.includes(user.id)) {
      participantIds.push(user.id);
    }

    if (participantIds.length < 2) {
      return c.json({ error: 'A group needs at least two participants' }, 400);
    }

    const uniqueIds = [...new Set(participantIds)];

    const users = await db.query(
      `SELECT id FROM users WHERE id = ANY($1::bigint[])`,
      [uniqueIds]
    );

    if (users.rowCount !== uniqueIds.length) {
      return c.json({ error: 'One or more users not found' }, 404);
    }

    const client = await db.connect();

    try {
      await client.query('BEGIN');

      const result = await client.query(
        `INSERT INTO conversations (is_direct, is_group, name, owner_id)
         VALUES (FALSE, TRUE, $1, $2)
         RETURNING id, created_at`,
        [name, user.id]
      );

      const conversationId = result.rows[0].id;

      await client.query(
        `INSERT INTO conversation_participants (conversation_id, user_id)
         SELECT $1, unnest($2::bigint[])`,
        [conversationId, uniqueIds]
      );

      await client.query('COMMIT');

      return c.json({
        data: { id: conversationId, created_at: result.rows[0].created_at },
        existing: false,
      }, 201);
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
    }
  } catch (error) {
    console.error('Create conversation failed:', error.message);
    return c.json({ error: 'Could not create conversation' }, 500);
  }
});

// List conversations for the signed-in user
messages.get('/conversations', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  try {
    const result = await db.query(
      `SELECT c.id, c.is_direct, c.is_group, c.name, c.owner_id, c.created_at,
              (SELECT json_agg(json_build_object('id', u.id, 'username', u.username)
               ORDER BY u.username)
               FROM conversation_participants p2
               JOIN users u ON u.id = p2.user_id
               WHERE p2.conversation_id = c.id) AS participants
       FROM conversations c
       JOIN conversation_participants p ON p.conversation_id = c.id AND p.user_id = $1
       ORDER BY c.created_at DESC`,
      [user.id]
    );

    return c.json({ data: result.rows });
  } catch (error) {
    console.error('List conversations failed:', error.message);
    return c.json({ error: 'Could not list conversations' }, 500);
  }
});

// Invite users to a group conversation (owner only)
messages.post('/conversations/:id/invite', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  const id = c.req.param('id');

  if (!validId(id)) {
    return c.json({ error: 'Invalid conversation ID' }, 400);
  }

  try {
    const owner = await isOwner(id, user.id);

    if (!owner) {
      const participant = await isParticipant(id, user.id);

      if (!participant) {
        return c.json({ error: 'Not a participant in this conversation' }, 403);
      }

      return c.json({ error: 'Only the conversation owner can invite people' }, 403);
    }

    const body = await c.req.json();

    const inviteeIds = Array.isArray(body.user_ids)
      ? body.user_ids.map(Number).filter(id => Number.isInteger(id) && id > 0)
      : [];

    if (inviteeIds.length === 0) {
      return c.json({ error: 'At least one user id is required' }, 400);
    }

    const uniqueIds = [...new Set(inviteeIds)];

    const users = await db.query(
      `SELECT id FROM users WHERE id = ANY($1::bigint[])`,
      [uniqueIds]
    );

    if (users.rowCount !== uniqueIds.length) {
      return c.json({ error: 'One or more users not found' }, 404);
    }

    const already = await db.query(
      `SELECT user_id FROM conversation_participants
       WHERE conversation_id = $1 AND user_id = ANY($2::bigint[])`,
      [id, uniqueIds]
    );

    if (already.rowCount > 0) {
      return c.json({ error: 'One or more users are already participants' }, 409);
    }

    await db.query(
      `INSERT INTO conversation_participants (conversation_id, user_id)
       SELECT $1, unnest($2::bigint[])`,
      [id, uniqueIds]
    );

    return c.json({ invited: uniqueIds });
  } catch (error) {
    console.error('Invite failed:', error.message);
    return c.json({ error: 'Could not invite users' }, 500);
  }
});

// Send a message in a conversation
messages.post('/conversations/:id/messages', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  const id = c.req.param('id');

  if (!validId(id)) {
    return c.json({ error: 'Invalid conversation ID' }, 400);
  }

  try {
    const body = await c.req.json();

    const content = typeof body.content === 'string' ? body.content.trim() : '';

    if (!content || content.length > MAX_MESSAGE_LENGTH) {
      return c.json({
        error: `Message must be between 1 and ${MAX_MESSAGE_LENGTH} characters`,
      }, 400);
    }

    const participant = await isParticipant(id, user.id);

    if (!participant) {
      return c.json({ error: 'Not a participant in this conversation' }, 403);
    }

    const result = await db.query(
      `INSERT INTO messages (conversation_id, sender_id, content)
       VALUES ($1, $2, $3)
       RETURNING id, conversation_id, sender_id, content, created_at`,
      [id, user.id, content]
    );

    return c.json({ data: result.rows[0] }, 201);
  } catch (error) {
    console.error('Send message failed:', error.message);
    return c.json({ error: 'Could not send message' }, 500);
  }
});

// List messages in a conversation
messages.get('/conversations/:id/messages', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  const id = c.req.param('id');

  if (!validId(id)) {
    return c.json({ error: 'Invalid conversation ID' }, 400);
  }

  try {
    const participant = await isParticipant(id, user.id);

    if (!participant) {
      return c.json({ error: 'Not a participant in this conversation' }, 403);
    }

    const limit = Math.min(
      Math.max(Number(c.req.query('limit') ?? 50), 1),
      100
    );
    const before = c.req.query('before') ?? null;

    let query = `
      SELECT m.id, m.conversation_id, m.sender_id, u.username AS sender_username,
             m.content, m.created_at
      FROM messages m
      JOIN users u ON u.id = m.sender_id
      WHERE m.conversation_id = $1
    `;
    const params = [id];

    if (before) {
      query += ` AND m.created_at < $2`;
      params.push(before);
    }

    query += ` ORDER BY m.created_at DESC LIMIT $${params.length + 1}`;
    params.push(limit);

    const result = await db.query(query, params);

    return c.json({
      data: result.rows.reverse(),
      limit,
    });
  } catch (error) {
    console.error('List messages failed:', error.message);
    return c.json({ error: 'Could not list messages' }, 500);
  }
});

// Get conversation details + participants
messages.get('/conversations/:id', async (c) => {
  const user = await requireUser(c);

  if (!user) {
    return c.json({ error: 'Not authenticated' }, 401);
  }

  const id = c.req.param('id');

  if (!validId(id)) {
    return c.json({ error: 'Invalid conversation ID' }, 400);
  }

  try {
    const participant = await isParticipant(id, user.id);

    if (!participant) {
      return c.json({ error: 'Not a participant in this conversation' }, 403);
    }

    const conversation = await db.query(
      `SELECT id, is_direct, name, owner_id, created_at FROM conversations WHERE id = $1`,
      [id]
    );

    if (!conversation.rowCount) {
      return c.json({ error: 'Conversation not found' }, 404);
    }

    const participants = await db.query(
      `SELECT u.id, u.username, u.created_at
       FROM conversation_participants p
       JOIN users u ON u.id = p.user_id
       WHERE p.conversation_id = $1
       ORDER BY u.username`,
      [id]
    );

    return c.json({
      data: {
        ...conversation.rows[0],
        is_group: !conversation.rows[0].is_direct,
        participants: participants.rows,
      },
    });
  } catch (error) {
    console.error('Get conversation failed:', error.message);
    return c.json({ error: 'Could not get conversation' }, 500);
  }
});
